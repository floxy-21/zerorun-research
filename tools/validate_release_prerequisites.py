"""Fail closed unless exact-commit release prerequisite receipts are complete."""

from __future__ import annotations

import argparse
import ast
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 release-test environment.
    import tomli as tomllib

from zerorun.bounded_json import JsonLimits, loads_bounded_json
from zerorun.model import ConfigurationError


MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_WHEEL_BYTES = 32 * 1024 * 1024
MAX_WHEEL_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_WHEEL_MEMBERS = 5_000
ROUNDING_REPLAY_TOLERANCE_MS = 0.002
EXPECTED_PACKAGE_SUMMARY = (
    "Conservative deterministic test-result reuse for AI coding agents and CI"
)
EXPECTED_REQUIRES_PYTHON = ">=3.10"
EXPECTED_LICENSE_EXPRESSION = "MIT"
EXPECTED_BUILD_GENERATOR = "setuptools (84.0.0)"
EXPECTED_PACKAGE_BUILD_TOOLS = {
    "build": "1.6.0",
    "pip": "26.2.1",
    "setuptools": "84.0.0",
    "wheel": "0.48.0",
}
EXPECTED_PACKAGE_BUILD_PYTHON = {
    "implementation": "CPython",
    "version": "3.12.13",
    "cache_tag": "cpython-312",
}
MAX_ATTESTED_FILE_BYTES = 512 * 1024 * 1024
EXPECTED_CONSOLE_SCRIPTS = (
    "[console_scripts]",
    "zerorun = zerorun.cli:main",
    "zerorun-pytest-activate = zerorun.pytest_review_cli:activate_main",
    "zerorun-pytest-prepare = zerorun.pytest_prepare_cli:main",
    "zerorun-pytest-review = zerorun.pytest_review_cli:review_main",
)
COMPARISON_COVERAGE_BASIS = (
    "the independent shadow executed the exact full collection; therefore every "
    "reused node is covered when all per-node shadow outcomes are complete and "
    "non-failing; because the product does not export reused node IDs, any shadow "
    "failure makes a reuse-bearing observation invalid"
)
PERFORMANCE_SCHEMA = "zerorun.product-generalization.aggregate.v1"
COMMERCIAL_SCHEMA = "zerorun.commercial-compatibility-aggregate.v1"
OUTPUT_SCHEMA = "zerorun.release-prerequisites.v1"
EXPECTED_COMMERCIAL_SELECTION_SHA256 = (
    "c3ecd05a6a5432c55bac1ba3eff3f448e10b778572b1b11ab2760709d0509061"
)
REFERENCE_GATE = {
    "min_compute_efficiency": 5.0,
    "min_p95_reduction_percent": 50.0,
}
PERFORMANCE_ACTIVATION_EVIDENCE_KIND = (
    "mechanical-synthetic-formative-fixture"
)
PERFORMANCE_PROTOCOL_DISCLOSURE = (
    "three previously seen frozen holdouts rerun as regression evidence plus "
    "two repositories frozen before performance measurement; 20 first-parent "
    "transitions each; unchanged 5x compute / 50% p95 reference line; "
    "performance misses remain visible and are not release-gate passes"
)
PERFORMANCE_RELEASE_GATE_DISCLOSURE = (
    "strict: every workload must pass safety, >=5x primary end-to-end "
    "plain-pytest-to-ZeroRun speedup, and >=50% p95 wall-time reduction"
)
EXPECTED_WORKLOADS = {
    "packaging": "old-regression",
    "pycparser": "old-regression",
    "colorama": "old-regression",
    "click": "new-unseen",
    "more-itertools": "new-unseen",
}
EXPECTED_WORKLOAD_DETAILS = {
    "packaging": {
        "upstream_repo": "pypa/packaging",
        "frozen_sha": "10590c194edb33c82f84a127883d6097c56b7840",
        "targets": ["tests/test_tags.py"],
        "extra_requirements": ["pretend"],
    },
    "pycparser": {
        "upstream_repo": "eliben/pycparser",
        "frozen_sha": "10d17757e282d8af5426d6df4d55eb394042b550",
        "targets": ["tests/test_c_parser.py"],
        "extra_requirements": [],
    },
    "colorama": {
        "upstream_repo": "tartley/colorama",
        "frozen_sha": "841634ed2a0da5d5ac2d867db533da8131266cb2",
        "targets": [
            "colorama/tests/ansi_test.py",
            "colorama/tests/ansitowin32_test.py",
            "colorama/tests/initialise_test.py",
            "colorama/tests/isatty_test.py",
            "colorama/tests/winterm_test.py",
        ],
        "extra_requirements": [],
    },
    "click": {
        "upstream_repo": "pallets/click",
        "frozen_sha": "36baa15ff831b939a22bc527cd76ce653ef6f66d",
        "targets": ["tests/test_arguments.py"],
        "extra_requirements": [],
    },
    "more-itertools": {
        "upstream_repo": "more-itertools/more-itertools",
        "frozen_sha": "d92f081a089714e0aa92434c797fdd1a06da1290",
        "targets": ["tests/test_more.py"],
        "extra_requirements": [],
    },
}
EXPECTED_CORPUS_COMMITS = {
    "packaging": [
        "2d90d060e3e3170f95b85f3cc88fc314df186058",
        "3c6e2aabd76bacb3f6b08a356074ce4662fd4506",
        "4794f1c9b43e8e49eeb36d730329a6f8282ce7b5",
        "ef91ddbe6891b87f687e95628224901bf06da58d",
        "2945ea803e4ced5de97e1741f98bf6b6ca164307",
        "902ca322a745e135c3d49af5ab66eca4fce084c5",
        "f8630ea2b7c810de81841459dcdb481d5b7a1318",
        "de6580e6f077a82b2b10161fd2e80e57b8d52854",
        "f14a0933259121eb9b986bc1b7924910755d535d",
        "f173f774d88b4dc962cc37eb2e707fd8b4838f71",
        "9645290b6ccbe6c437fa26157660e69df9a0f2ad",
        "e722a4a7619bfe83ab17cf43114ff0f806bb89c7",
        "56dd80bca9e09db6c4facae66c80b2d0a6197098",
        "4f0607228706b1dacf2c76c118a042b3ea197590",
        "4840c3a6817fbd0831f7e520c9a55367472a4a08",
        "55cbf1b9426f44455fa1a9e0836f1fc082cc8452",
        "053c884615f2e83d80705251b447769ec2599653",
        "b9d249f585ba0bd3100ea5f2fd1017b215c12be9",
        "de7d962e83c10059bc40c9347864844e048069b7",
        "bb935cfac9c0ca9e53fe343836b95787f67155e4",
        "10590c194edb33c82f84a127883d6097c56b7840",
    ],
    "pycparser": [
        "ece85e01efe4dcd13f8d5ccd1bb8c191e9d5f392",
        "20306a2d3cd76fe29bd78bdc94c95b22f0aa62c7",
        "092bfe60b21391c0f83c1a3351b5d99d7cb8a266",
        "b05ad63a261d7820a623a8dbf3b5167e73802fb1",
        "d1399730ee4d86716d4f9f5cee7207df78eb435a",
        "9cd0027f874fcceacca87fdfb163a387bf4d56b6",
        "8c219ec62ea3bf835c473954f30ea9b1202069eb",
        "76a93395c20f665e10277b3461b555dc124e1dd9",
        "bed9e4cbf09e6b6df9ef9a939f321928f188ad17",
        "fe398c14f8ec3fbdfaf918c45576bf8c841172c3",
        "9d14b1595a2ba3ce694dd216038bf07905a5d438",
        "63a4e9c90638699712abf15ba06b14490762be89",
        "89c9f3d8642b9ec84cfdd6921faffaba7e95c43c",
        "313d292c37e37bd498d8d867d247b596590e166f",
        "9e8fccaf67be2b03f3011c6ff7cb936c398957d4",
        "5d07ae81d84be931bfed55c2dd75de9d56f56412",
        "eb80ed2f8b920584803790cd5287941f6a96aee8",
        "ff1bf54defba3dfd15c58501e1902f0ba6f832b4",
        "731b3ae203095f631060cf38151914ee8a1e5cf3",
        "450ccd451bd3c90df06a5089bf3b2abb13d6d2d9",
        "10d17757e282d8af5426d6df4d55eb394042b550",
    ],
    "colorama": [
        "60e1f541cada2d3c90c3b237adae7d542030bac0",
        "ab64cfac23fd70b9bf2944acd985e3673a1f09e1",
        "52f4cfd7af3a779b0ba8a7ee320d99c0bfec9ce8",
        "0ae5ef2f9ab89335fb5b50933b75323178cb38bc",
        "7991d34773c72db791eb09d9ec349212d080fbc0",
        "54b89c33b2448a1a0fab67ab020a902622b9a932",
        "832f14ce9a4d00c2dfb88e8cb02e2defadb76ba9",
        "cb83041b25a6962d8124647e8741cfa0ab139994",
        "f55f72e9d3950276679da91ba6dc713cefc603db",
        "a45949b3252633060261c9666b6aada4b2f2f020",
        "3de9f013df4b470069d03d250224062e8cf15c49",
        "6f03975cc95a88b6d3edfbb248f5b92f3e267271",
        "43491734a4738adb13a060edf7424f222e5e6f0c",
        "21c4b94fe21ce29c85c896ace828da24b7527641",
        "7f1a5933851363abbdc24a51e76829bb23a2793b",
        "136808718af8b9583cb2eed1756ed6972eda4975",
        "d33ab03f5052182b9bead5a5ff5f664d3e682f46",
        "e6fba72abfeb6085c5fa1b2d3fdfdcfaea52fee8",
        "4dff0bb325a5f723cb6e07845ad3ad93b450131a",
        "406153f134db6b5c5391f223be46f9e8a902e6b6",
        "841634ed2a0da5d5ac2d867db533da8131266cb2",
    ],
    "click": [
        "b551fe85714c906565303fae98136d7c72b252ea",
        "7df2f82305f20f1611a9668b38d26933b648d807",
        "333c28d79cd982990ee98eef61ec20ab1a4f38ba",
        "cfa01eeb7894a408af70b29d28c0b24f8680f9fb",
        "5e906a8afb67242dffdd91404dbf31f469054b15",
        "398f9154317f6c54bf98fe3359672ad5cb851585",
        "42235de05e38e3b9c9346662a3c6b6a36901b8a1",
        "5aa8ac43527f91c4c801a50b485c09576715d340",
        "00e592cea702e0b2caa0dee42489fdb1c22cd845",
        "9c4dfdaebe0e6b2aabc566eb81f6f10eb5cd6ea1",
        "8b44edfff7d9a6c895fa804148c16b3a0bc9efb5",
        "150d1071d69c5cdad7de78590013ffe56cf9e3bb",
        "fc5c7f45da0a4443f60ba2d322fe4bf829977739",
        "cbd7a4109da16ce58f54c2a618b4c986e3041fcf",
        "f36d58bbd7f188178de2d4fe1d0292c510375ca7",
        "61b69e967e525bd502f5bf42def4d551e761fe0e",
        "2103e157683c5e4cadc8ee1838df526a54bde9a4",
        "e1fd5946ab26aaf372009eaff1acf947140b40fb",
        "2c8cd3ac958a7eb316d67f2d316c27086c4c0369",
        "68e7ea7228ca144c52e4d1d282cc09da59f7771f",
        "36baa15ff831b939a22bc527cd76ce653ef6f66d",
    ],
    "more-itertools": [
        "fe24c338d8c3264a82cdc2bed49fb156cfd11a86",
        "ed86a1528aa015f219f8d3385ea2ebd3f63a5212",
        "d82024d025ec48f1197f92d5b66560a537a78184",
        "8ff2d60e1462a6857f5a9b153d7be474f82da8b5",
        "c2859de7ae7b791b77c33462a58f81f9a4d9d5d6",
        "cb75bb9c55f7ed3e77ce599097e1ba8da411746d",
        "301c957c8a50eca09c3b6eae416508a91487b552",
        "da37f9de442b69fbcaa9f54fb042c2a6999473a6",
        "f9cdcb990dbb87a9748e61a9a28c2ad737e60452",
        "516f0a80fb7c2c8562dbb5e318fc2a3d44f4171f",
        "9ddc55c57390707d97d96302eea1992919c8d930",
        "7ff676d95968fb0a85f2527056c575cba039303c",
        "3a259350f28cd17f09b4f0af21c6ebc03cf725cf",
        "c69f0f9129ff8d26102219f50b60088353be7edf",
        "cecd215fe1c6fdc639b9eb837cee93bba09d2952",
        "8a9dd135ea32301e9eaf6f179384db464aeee6b6",
        "2fe1b2eeb9d75f994113fe3ac76d14b6bcd6fb10",
        "30ada7dd597316a3ef967911477e0a1d9f55eefe",
        "bc8be1013ac90fe3c1df8aae646b02605d578818",
        "a826a4e09e3f2782822c71da6670e9275735ad3e",
        "d92f081a089714e0aa92434c797fdd1a06da1290",
    ],
}
PERFORMANCE_METHODOLOGY = "zerorun.product-generalization-counterbalanced-e2e.v2"
PERFORMANCE_RUNTIME_IMAGE = (
    "docker.io/library/python@sha256:"
    "9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
)
PERFORMANCE_RUNTIME_IMPLEMENTATION = "CPython"
PERFORMANCE_RUNTIME_PYTHON_VERSION = "3.12.14"
PERFORMANCE_RUNTIME_ATTESTATION = (
    "verified during the single hash-locked dependency-layer build; the "
    "exact pinned image and private read-only layer bytes are re-attested "
    "for each isolated trajectory"
)
PERFORMANCE_DEPENDENCY_LAYER_SCHEMA = "zerorun.generalization-dependency-layer.v1"
PERFORMANCE_MATERIALIZATION_SCHEMA = (
    "zerorun.generalization-private-dependency-materialization.v1"
)
PERFORMANCE_DEPENDENCY_INSTALL_ARGUMENTS = [
    "install",
    "--disable-pip-version-check",
    "--no-input",
    "--no-cache-dir",
    "--no-compile",
    "--require-hashes",
    "--only-binary=:all:",
    "--target",
    "/zerorun-env/site-packages",
    "--requirement",
    "/zerorun-env/runtime-requirements.txt",
]
PERFORMANCE_DEPENDENCY_BOOTSTRAP_SHA256 = (
    "431be6c9136f8850489a2bd70a5b40c6d88c7a6ab2847b5ff2792c36e6856fbe"
)
PERFORMANCE_DEPENDENCY_INSTALL_ARGUMENTS_SHA256 = (
    "1917fa826e1df105404a601cb684896322383ed45548bd7199bd0b8c8a368ed5"
)
PERFORMANCE_DEPENDENCY_WRAPPER_SHA256 = (
    "7219e190ec967b530b48bc9a4ee72fb0b35fbe01d310ad5fdae5411082489d9c"
)
PERFORMANCE_DEPENDENCY_MANIFEST_SHA256 = (
    "8f76906073db1504ed464aa0416064bffb3c825d9bf2bf5acad26225914d2c11"
)
PERFORMANCE_RESOURCE_ARGS = [
    "--cpus",
    "2",
    "--memory",
    "2g",
    "--memory-swap",
    "2g",
    "--pids-limit",
    "512",
]
PRIMARY_COST_COMPONENTS = [
    "symmetric_environment_bootstrap_charged_to_each_arm",
    "direct_seed_charged_to_direct",
    "zerorun_qualification_charged_to_zerorun",
    "zerorun_activation_charged_to_zerorun",
    "zerorun_seed_charged_to_zerorun",
    "automatic_refresh_charged_to_zerorun",
]
EXPECTED_CODEX_REPORTED_VERSION = "codex-cli 0.153.3"
EXPECTED_MANAGED_SKILL_SHA256 = (
    "564a9804d2587d8164a509a864e31e9ca5320f8d2af18648be4094eff9ae9b96"
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RELEASE_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_JSON_BYTES,
    max_depth=64,
    max_values=1_000_000,
    max_object_members=100_000,
    max_structural_tokens=4_000_000,
    max_number_chars=256,
    max_string_chars=1024 * 1024,
    max_total_string_chars=24 * 1024 * 1024,
)


class PrerequisiteError(ValueError):
    """A release prerequisite is missing, malformed, or adverse."""


def _read_regular_bytes(path: Path, *, label: str, max_bytes: int) -> bytes:
    path = Path(os.path.abspath(path.expanduser()))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise PrerequisiteError(f"{label} must be a regular non-link file")
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_file_attributes", 0) & _REPARSE_POINT
                or identity != (before.st_dev, before.st_ino)
                or opened.st_size <= 0
                or opened.st_size > max_bytes
            ):
                raise PrerequisiteError(f"{label} size or identity is invalid")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            current = os.lstat(path)
            if (
                len(raw) != opened.st_size
                or len(raw) > max_bytes
                or (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                    getattr(after, "st_ctime_ns", None),
                )
                != (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_mode,
                    opened.st_size,
                    opened.st_mtime_ns,
                    getattr(opened, "st_ctime_ns", None),
                )
                or not stat.S_ISREG(current.st_mode)
                or getattr(current, "st_file_attributes", 0) & _REPARSE_POINT
                or (
                    current.st_dev,
                    current.st_ino,
                    current.st_mode,
                    current.st_size,
                    current.st_mtime_ns,
                )
                != (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_mode,
                    opened.st_size,
                    opened.st_mtime_ns,
                )
            ):
                raise PrerequisiteError(f"{label} changed while being read")
        finally:
            os.close(descriptor)
    except PrerequisiteError:
        raise
    except OSError as exc:
        raise PrerequisiteError(f"could not safely read {label}: {exc}") from exc
    return raw


def _load_object_with_digest(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    raw = _read_regular_bytes(path, label=label, max_bytes=MAX_JSON_BYTES)
    try:
        payload = loads_bounded_json(raw, label=label, limits=_RELEASE_JSON_LIMITS)
    except ConfigurationError as exc:
        raise PrerequisiteError(f"{label} is not bounded strict UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PrerequisiteError(f"{label} must contain a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    return _load_object_with_digest(path, label=label)[0]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PrerequisiteError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise PrerequisiteError(f"{label} must be a finite number")
    return result


def _finite_nonnegative(value: object, *, label: str) -> float:
    result = _finite_number(value, label=label)
    if result < 0:
        raise PrerequisiteError(f"{label} must be nonnegative")
    return result


def _finite_positive(value: object, *, label: str) -> float:
    result = _finite_number(value, label=label)
    if result <= 0:
        raise PrerequisiteError(f"{label} must be positive")
    return result


def _close(reported: float, recomputed: float, *, places: int) -> bool:
    return math.isclose(
        reported,
        recomputed,
        rel_tol=0.0,
        abs_tol=(0.5 * (10.0 ** -places)) + 1e-12,
    )


def _close_rounded_ms(reported: float, recomputed: float) -> bool:
    """Allow only the derived worst-case error from nested 0.001-ms rounding."""

    return math.isclose(
        reported,
        recomputed,
        rel_tol=0.0,
        abs_tol=ROUNDING_REPLAY_TOLERANCE_MS,
    )


def _nearest_rank(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _project_version(root: Path) -> str:
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    tree = ast.parse((root / "zerorun" / "__init__.py").read_text(encoding="utf-8"))
    module_versions = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
    ]
    if module_versions != [declared] or not isinstance(declared, str) or not declared:
        raise PrerequisiteError("package version declarations do not agree")
    return declared


def _candidate_commit_epoch(root: Path, candidate_sha: str) -> int:
    if SHA40.fullmatch(candidate_sha) is None:
        raise PrerequisiteError("release candidate commit is malformed")
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(root),
                "show",
                "-s",
                "--format=%H%n%ct",
                candidate_sha,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrerequisiteError("could not resolve release candidate commit time") from exc
    lines = completed.stdout.splitlines()
    if (
        completed.returncode != 0
        or lines[:1] != [candidate_sha]
        or len(lines) != 2
        or not lines[1].isdigit()
        or int(lines[1]) <= 0
    ):
        raise PrerequisiteError("release candidate commit time is unavailable")
    return int(lines[1])


def _validate_selection(selection: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if selection.get("schema") != "zerorun.commercial-compatibility-selection.v1":
        raise PrerequisiteError("commercial selection schema is unsupported")
    digest = selection.get("corpus_sha256")
    if (
        not isinstance(digest, str)
        or SHA64.fullmatch(digest) is None
        or digest != EXPECTED_COMMERCIAL_SELECTION_SHA256
    ):
        raise PrerequisiteError("commercial selection digest is malformed")
    unhashed = dict(selection)
    unhashed.pop("corpus_sha256", None)
    if hashlib.sha256(_canonical(unhashed)).hexdigest() != digest:
        raise PrerequisiteError("commercial selection digest mismatch")
    repositories = selection.get("repositories")
    if (
        not isinstance(repositories, list)
        or len(repositories) != 100
        or selection.get("repository_count") != 100
        or selection.get("distinct_repository_count") != 100
    ):
        raise PrerequisiteError("commercial selection is not exactly 100 repositories")
    identities: set[str] = set()
    for index, row in enumerate(repositories):
        if not isinstance(row, dict):
            raise PrerequisiteError(f"commercial selection row {index} is malformed")
        repository = row.get("repository")
        commit = row.get("commit")
        if (
            not isinstance(repository, str)
            or not repository
            or repository.casefold() in identities
            or not isinstance(commit, str)
            or SHA40.fullmatch(commit) is None
        ):
            raise PrerequisiteError(f"commercial selection row {index} identity is invalid")
        identities.add(repository.casefold())
    return repositories


def _validate_source_identity(
    source: object, *, candidate_sha: str, label: str, root: Path
) -> None:
    if not isinstance(source, dict) or source.get("stable") is not True:
        raise PrerequisiteError(f"{label} source identity is absent or unstable")
    pre = source.get("pre")
    if not isinstance(pre, dict) or pre != source.get("post"):
        raise PrerequisiteError(f"{label} source identity changed during measurement")
    git = pre.get("git")
    if (
        not isinstance(git, dict)
        or git.get("commit") != candidate_sha
        or git.get("engine_python_sources_dirty") is not False
        or git.get("trial_tool_dirty") is not False
        or source.get("caller_expected_engine_sha") != candidate_sha
    ):
        raise PrerequisiteError(f"{label} source identity is not the release candidate")
    if set(source) != {
        "pre",
        "post",
        "stable",
        "caller_expected_engine_sha",
        "caller_expected_engine_python_source_sha256",
        "caller_expected_trial_tool_sha256",
    }:
        raise PrerequisiteError(f"{label} source identity fields are malformed")
    if (
        pre.get("schema")
        != "zerorun.product-generalization-source-identity.v1"
        or pre.get("engine_version") != _project_version(root)
        or set(pre)
        != {
            "schema",
            "engine_version",
            "engine_python_source",
            "trial_tool",
            "git",
            "identity_sha256",
        }
    ):
        raise PrerequisiteError(f"{label} source attestation is malformed")
    engine = pre.get("engine_python_source")
    if not isinstance(engine, dict) or set(engine) != {
        "schema",
        "files",
        "file_count",
        "total_bytes",
        "sha256",
    }:
        raise PrerequisiteError(f"{label} engine-source attestation is malformed")
    files = engine.get("files")
    actual_paths = sorted(
        (path for path in (root / "zerorun").rglob("*.py")),
        key=lambda path: (path.relative_to(root).as_posix().casefold(), path.relative_to(root).as_posix()),
    )
    if (
        engine.get("schema") != "zerorun.engine-python-source-identity.v1"
        or not isinstance(files, list)
        or engine.get("file_count") != len(files)
        or len(files) != len(actual_paths)
        or [item.get("path") for item in files if isinstance(item, dict)]
        != [path.relative_to(root).as_posix() for path in actual_paths]
    ):
        raise PrerequisiteError(f"{label} engine-source inventory changed")
    total_bytes = 0
    for item, path in zip(files, actual_paths):
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "size", "sha256"}
            or path.is_symlink()
            or not path.is_file()
        ):
            raise PrerequisiteError(f"{label} engine-source row is unsafe")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if item.get("size") != len(raw) or item.get("sha256") != digest:
            raise PrerequisiteError(f"{label} engine-source bytes changed")
        total_bytes += len(raw)
    engine_payload = {"schema": engine["schema"], "files": files}
    if (
        engine.get("total_bytes") != total_bytes
        or engine.get("sha256") != hashlib.sha256(_canonical(engine_payload)).hexdigest()
        or source.get("caller_expected_engine_python_source_sha256")
        != engine.get("sha256")
    ):
        raise PrerequisiteError(f"{label} engine-source digest is invalid")
    tool = pre.get("trial_tool")
    tool_path = root / "tools" / "product_generalization_benchmark.py"
    if (
        not isinstance(tool, dict)
        or set(tool) != {"path", "size", "sha256"}
        or tool.get("path") != "tools/product_generalization_benchmark.py"
        or tool_path.is_symlink()
        or not tool_path.is_file()
    ):
        raise PrerequisiteError(f"{label} benchmark-tool attestation is malformed")
    tool_raw = tool_path.read_bytes()
    if (
        tool.get("size") != len(tool_raw)
        or tool.get("sha256") != hashlib.sha256(tool_raw).hexdigest()
        or source.get("caller_expected_trial_tool_sha256") != tool.get("sha256")
    ):
        raise PrerequisiteError(f"{label} benchmark-tool bytes changed")
    identity_payload = dict(pre)
    identity_digest = identity_payload.pop("identity_sha256", None)
    if (
        not isinstance(identity_digest, str)
        or SHA64.fullmatch(identity_digest) is None
        or identity_digest != hashlib.sha256(_canonical(identity_payload)).hexdigest()
    ):
        raise PrerequisiteError(f"{label} source identity digest is invalid")


def _validate_comparison_evidence(
    comparison: object,
    *,
    label: str,
    expected_direct_code: int | None = None,
    expected_product_code: int | None = None,
    expected_product_collection: str | None = None,
    expected_total_nodes: int | None = None,
    expected_reused_nodes: int | None = None,
) -> None:
    expected_fields = {
        "direct_exit_code",
        "zerorun_exit_code",
        "all_exit_codes_match",
        "exact_node_sequence_match",
        "product_snapshot_error",
        "product_collection_sha256",
        "product_snapshot_collection_sha256",
        "product_snapshot_nodeid_sha256",
        "product_snapshot_node_count",
        "independent_shadow_nodeid_sha256",
        "independent_shadow_node_count",
        "independent_shadow_exit_code",
        "independent_shadow_complete_per_node_outcomes",
        "independent_shadow_all_nodes_non_failing",
        "reused_node_fresh_shadow_coverage",
        "reused_node_fresh_shadow_denominator",
        "reuse_shadow_valid",
        "comparison_pass",
        "coverage_basis",
    }
    if not isinstance(comparison, dict) or set(comparison) != expected_fields:
        raise PrerequisiteError(f"{label} comparison evidence has the wrong schema")
    direct_code = comparison.get("direct_exit_code")
    product_code = comparison.get("zerorun_exit_code")
    shadow_code = comparison.get("independent_shadow_exit_code")
    snapshot_count = comparison.get("product_snapshot_node_count")
    shadow_count = comparison.get("independent_shadow_node_count")
    denominator = comparison.get("reused_node_fresh_shadow_denominator")
    coverage = comparison.get("reused_node_fresh_shadow_coverage")
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (direct_code, product_code, shadow_code)
    ) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (snapshot_count, shadow_count, denominator, coverage)
    ):
        raise PrerequisiteError(f"{label} comparison counts or exits are malformed")
    product_collection = comparison.get("product_collection_sha256")
    snapshot_collection = comparison.get("product_snapshot_collection_sha256")
    product_nodeids = comparison.get("product_snapshot_nodeid_sha256")
    shadow_nodeids = comparison.get("independent_shadow_nodeid_sha256")
    digest_fields = (
        product_collection,
        snapshot_collection,
        product_nodeids,
        shadow_nodeids,
    )
    digests_valid = all(
        isinstance(value, str) and SHA64.fullmatch(value) is not None
        for value in digest_fields
    )
    exact_collection = bool(
        comparison.get("product_snapshot_error") is None
        and digests_valid
        and product_collection == snapshot_collection
        and product_nodeids == shadow_nodeids
        and snapshot_count == shadow_count
        and (expected_total_nodes is None or snapshot_count == expected_total_nodes)
    )
    exits_match = direct_code == product_code == shadow_code
    reused = denominator if expected_reused_nodes is None else expected_reused_nodes
    reuse_shadow_valid = bool(
        reused == 0
        or (
            exact_collection
            and direct_code == 0
            and product_code == 0
            and shadow_code == 0
            and comparison.get("independent_shadow_complete_per_node_outcomes")
            is True
            and comparison.get("independent_shadow_all_nodes_non_failing") is True
        )
    )
    comparison_pass = exits_match and exact_collection and reuse_shadow_valid
    if (
        (expected_direct_code is not None and direct_code != expected_direct_code)
        or (expected_product_code is not None and product_code != expected_product_code)
        or (
            expected_product_collection is not None
            and product_collection != expected_product_collection
        )
        or (expected_reused_nodes is not None and denominator != expected_reused_nodes)
        or comparison.get("all_exit_codes_match") is not exits_match
        or comparison.get("exact_node_sequence_match") is not exact_collection
        or comparison.get("reuse_shadow_valid") is not reuse_shadow_valid
        or coverage != (reused if reuse_shadow_valid else 0)
        or comparison.get("comparison_pass") is not comparison_pass
        or comparison.get("coverage_basis") != COMPARISON_COVERAGE_BASIS
    ):
        raise PrerequisiteError(f"{label} comparison evidence does not replay")


def _validate_timing_mapping(
    value: object, *, label: str, required_keys: set[str] | None = None
) -> dict[str, float]:
    if (
        not isinstance(value, dict)
        or any(not isinstance(key, str) or not key for key in value)
        or (required_keys is not None and set(value) != required_keys)
    ):
        raise PrerequisiteError(f"{label} timing map is malformed")
    return {
        key: _finite_nonnegative(item, label=f"{label} {key}")
        for key, item in value.items()
    }


def _sum_timing_mappings(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    names = sorted({name for row in rows for name in row})
    return {
        name: round(math.fsum(float(row.get(name, 0.0)) for row in rows), 3)
        for name in names
    }


def _validate_performance_workload(
    row: Mapping[str, Any],
    *,
    name: str,
    holdout_class: str,
    candidate_sha: str,
    runtime_lock: str,
    root: Path,
) -> dict[str, Any]:
    details = EXPECTED_WORKLOAD_DETAILS[name]
    if (
        row.get("schema") != "zerorun.product-generalization.v1"
        or row.get("engine_sha") != candidate_sha
        or row.get("holdout_class") != holdout_class
        or row.get("upstream_repo") != details["upstream_repo"]
        or row.get("frozen_sha") != details["frozen_sha"]
        or row.get("targets") != details["targets"]
        or row.get("extra_requirements") != details["extra_requirements"]
        or row.get("case_count") != 20
        or row.get("primary_horizon_transitions") != 20
        or row.get("methodology_version") != PERFORMANCE_METHODOLOGY
        or row.get("runtime_image") != PERFORMANCE_RUNTIME_IMAGE
        or row.get("runtime_python_implementation")
        != PERFORMANCE_RUNTIME_IMPLEMENTATION
        or row.get("runtime_python_version")
        != PERFORMANCE_RUNTIME_PYTHON_VERSION
        or row.get("runtime_python_attestation") != PERFORMANCE_RUNTIME_ATTESTATION
        or row.get("runtime_requirements_sha256") != runtime_lock
        or row.get("frozen_runtime_environment") is not True
        or row.get("reference_gate") != REFERENCE_GATE
        or row.get("reference_gate_metric")
        != "end_to_end_plain_pytest_to_zerorun_speedup"
        or row.get("reference_gate_requires_both_thresholds_per_workload") is not True
        or row.get("primary_metric")
        != "end_to_end_plain_pytest_to_zerorun_speedup"
        or row.get("counterbalanced_trajectory_count") != 2
        or row.get("repetitions_per_transition_per_arm") != 2
        or row.get("safety_pass") is not True
        or row.get("performance_gate_pass") is not True
        or row.get("adverse_audits_pass") is not True
    ):
        raise PrerequisiteError(
            f"{name} changed its frozen performance contract or did not pass 5x/50"
        )

    envelope = row.get("container_resource_envelope")
    if envelope != {
        "docker_args": PERFORMANCE_RESOURCE_ARGS,
        "identical_for_plain_pytest_and_zerorun": True,
    }:
        raise PrerequisiteError(f"{name} did not use the equal frozen resource envelope")

    timing = row.get("timing_semantics")
    if (
        not isinstance(timing, dict)
        or timing.get("gate_inputs_unchanged") is not True
        or timing.get("compatibility_aliases_equal_primary_end_to_end") is not True
        or not isinstance(timing.get("direct"), str)
        or "uninstrumented ordinary pytest" not in timing["direct"]
        or not isinstance(timing.get("zerorun"), str)
        or "qualification" not in timing["zerorun"]
        or not isinstance(timing.get("shadow_oracle"), str)
        or "excluded" not in timing["shadow_oracle"]
    ):
        raise PrerequisiteError(f"{name} has invalid timing semantics")

    schedule = row.get("trajectory_schedule")
    seed_material = json.dumps(
        {
            "methodology": PERFORMANCE_METHODOLOGY,
            "workload": name,
            "frozen_sha": details["frozen_sha"],
            "targets": details["targets"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    seed_digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    expected_orders = ["plain-then-zerorun", "zerorun-then-plain"]
    if int(seed_digest[:2], 16) & 1:
        expected_orders.reverse()
    if schedule != {
        "seed_material": seed_material,
        "seed_sha256": seed_digest,
        "execution_order": expected_orders,
        "counterbalance_complete": True,
    }:
        raise PrerequisiteError(f"{name} counterbalanced schedule is invalid")

    corpus = row.get("corpus_commits")
    if (
        not isinstance(corpus, list)
        or len(corpus) != 21
        or len(set(corpus)) != 21
        or any(not isinstance(value, str) or SHA40.fullmatch(value) is None for value in corpus)
        or corpus != EXPECTED_CORPUS_COMMITS[name]
    ):
        raise PrerequisiteError(f"{name} frozen first-parent corpus is invalid")

    ledger = row.get("cost_ledger_ms")
    if not isinstance(ledger, dict) or set(ledger) != set(PRIMARY_COST_COMPONENTS):
        raise PrerequisiteError(f"{name} primary cost ledger is incomplete")
    costs = {
        component: _finite_nonnegative(
            ledger[component], label=f"{name} cost {component}"
        )
        for component in PRIMARY_COST_COMPONENTS
    }
    expected_direct_overhead = (
        costs["symmetric_environment_bootstrap_charged_to_each_arm"]
        + costs["direct_seed_charged_to_direct"]
    )
    expected_zerorun_overhead = math.fsum(
        (
            costs["symmetric_environment_bootstrap_charged_to_each_arm"],
            costs["zerorun_qualification_charged_to_zerorun"],
            costs["zerorun_activation_charged_to_zerorun"],
            costs["zerorun_seed_charged_to_zerorun"],
            costs["automatic_refresh_charged_to_zerorun"],
        )
    )
    accounting = row.get("primary_cost_accounting")
    if not isinstance(accounting, dict):
        raise PrerequisiteError(f"{name} primary cost accounting is absent")
    direct_overhead = _finite_nonnegative(
        accounting.get("direct_overhead_before_horizon_amortization_ms"),
        label=f"{name} direct overhead",
    )
    zerorun_overhead = _finite_nonnegative(
        accounting.get("zerorun_overhead_before_horizon_amortization_ms"),
        label=f"{name} ZeroRun overhead",
    )
    if (
        accounting.get("schema")
        != "zerorun.primary-end-to-end-cost-accounting.v1"
        or accounting.get("required_components") != PRIMARY_COST_COMPONENTS
        or accounting.get("all_required_components_present") is not True
        or accounting.get("all_components_finite_and_nonnegative") is not True
        or accounting.get("horizon_transitions") != 20
        or accounting.get("reconciled_before_gate_evaluation") is not True
        or not math.isclose(direct_overhead, expected_direct_overhead, abs_tol=0.005)
        or not math.isclose(zerorun_overhead, expected_zerorun_overhead, abs_tol=0.005)
    ):
        raise PrerequisiteError(f"{name} primary cost accounting does not reconcile")

    excluded = row.get("excluded_validation_wall_ms")
    expected_excluded = {
        "independent_fresh_shadow_excluded_from_primary_end_to_end",
        "adverse_audits_excluded_from_primary_end_to_end",
    }
    if not isinstance(excluded, dict) or set(excluded) != expected_excluded:
        raise PrerequisiteError(f"{name} excluded validation timing is malformed")
    for component in expected_excluded:
        _finite_nonnegative(excluded[component], label=f"{name} excluded {component}")

    profile_refresh = row.get("profile_refresh")
    if (
        not isinstance(profile_refresh, dict)
        or isinstance(profile_refresh.get("required_observations"), bool)
        or not isinstance(profile_refresh.get("required_observations"), int)
        or profile_refresh["required_observations"] < 0
        or profile_refresh.get("automatic_refreshes_performed") != 0
        or profile_refresh.get("fail_closed_violations") != 0
        or profile_refresh.get("runtime_refresh_cost_ms") != 0.0
        or profile_refresh.get("external_human_review_time_measured") is not False
        or costs["automatic_refresh_charged_to_zerorun"] != 0.0
    ):
        raise PrerequisiteError(f"{name} profile refresh did not fail closed")

    dependency_layer = row.get("frozen_dependency_layer")
    if not isinstance(dependency_layer, dict):
        raise PrerequisiteError(f"{name} frozen dependency-layer provenance is absent")
    construction_identity = dependency_layer.get("construction_identity_sha256")
    content_tree = dependency_layer.get("content_tree_sha256")
    layer_timing = dependency_layer.get("timing_ms")
    construction_contract = dependency_layer.get("construction_contract")
    expected_construction_contract = {
        "schema": PERFORMANCE_DEPENDENCY_LAYER_SCHEMA,
        "methodology_version": PERFORMANCE_METHODOLOGY,
        "runtime": {
            "image": PERFORMANCE_RUNTIME_IMAGE,
            "platform": "linux/amd64",
            "python_implementation": PERFORMANCE_RUNTIME_IMPLEMENTATION,
            "python_implementation_id": "cpython",
            "python_version": PERFORMANCE_RUNTIME_PYTHON_VERSION,
        },
        "requirements": {
            "path": "ci/generalization-runtime-requirements.txt",
            "size": (root / "ci" / "generalization-runtime-requirements.txt").stat().st_size,
            "sha256": runtime_lock,
        },
        "extra_requirements": details["extra_requirements"],
        "installer": {
            "entrypoint": ["/usr/local/bin/python", "-I", "-c"],
            "bootstrap_sha256": PERFORMANCE_DEPENDENCY_BOOTSTRAP_SHA256,
            "arguments": PERFORMANCE_DEPENDENCY_INSTALL_ARGUMENTS,
            "pull_policy": "never",
            "container_user": "exact-private-staging-owner",
            "capabilities": "drop-all",
            "no_new_privileges": True,
            "resource_args": PERFORMANCE_RESOURCE_ARGS,
        },
        "wrapper_sha256": PERFORMANCE_DEPENDENCY_WRAPPER_SHA256,
        "manifest_sha256": PERFORMANCE_DEPENDENCY_MANIFEST_SHA256,
    }
    try:
        encoded_construction = json.dumps(
            construction_contract,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded_construction = b""
    recomputed_construction_identity = hashlib.sha256(
        encoded_construction
    ).hexdigest()
    layer_integer_fields = (
        dependency_layer.get("file_count"),
        dependency_layer.get("directory_count"),
        dependency_layer.get("total_bytes"),
        dependency_layer.get("path_bytes"),
    )
    if (
        dependency_layer.get("schema") != PERFORMANCE_DEPENDENCY_LAYER_SCHEMA
        or construction_contract != expected_construction_contract
        or recomputed_construction_identity != construction_identity
        or SHA64.fullmatch(str(construction_identity)) is None
        or SHA64.fullmatch(str(content_tree)) is None
        or dependency_layer.get("content_addressed_directory_name") != content_tree
        or dependency_layer.get("runtime_image") != PERFORMANCE_RUNTIME_IMAGE
        or dependency_layer.get("runtime_requirements_sha256") != runtime_lock
        or dependency_layer.get("extra_requirements") != details["extra_requirements"]
        or SHA64.fullmatch(
            str(dependency_layer.get("installer_bootstrap_sha256"))
        )
        is None
        or dependency_layer.get("installer_bootstrap_sha256")
        != PERFORMANCE_DEPENDENCY_BOOTSTRAP_SHA256
        or SHA64.fullmatch(
            str(dependency_layer.get("installer_arguments_sha256"))
        )
        is None
        or dependency_layer.get("installer_arguments_sha256")
        != PERFORMANCE_DEPENDENCY_INSTALL_ARGUMENTS_SHA256
        or SHA64.fullmatch(str(dependency_layer.get("wrapper_sha256"))) is None
        or dependency_layer.get("wrapper_sha256")
        != PERFORMANCE_DEPENDENCY_WRAPPER_SHA256
        or SHA64.fullmatch(str(dependency_layer.get("manifest_sha256"))) is None
        or dependency_layer.get("manifest_sha256")
        != PERFORMANCE_DEPENDENCY_MANIFEST_SHA256
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in layer_integer_fields
        )
        or dependency_layer.get("file_count", 0) < 2
        or dependency_layer.get("directory_count", 0) < 1
        or dependency_layer.get("read_only_mode_and_content_verified") is not True
        or dependency_layer.get("source_hardlinks_rejected") is not True
        or dependency_layer.get("build_count") != 1
        or dependency_layer.get("content_addressed") is not True
        or dependency_layer.get("private_materialization_count") != 2
        or dependency_layer.get("private_materialization_method")
        != "exclusive-byte-copy-no-hardlinks"
        or dependency_layer.get("shared_writable_dependency_state") is not False
        or dependency_layer.get("independent_result_cache_count") != 2
        or dependency_layer.get("cleanup_confirmed_before_receipt_return") is not True
        or not isinstance(layer_timing, dict)
        or set(layer_timing)
        != {
            "build_once",
            "private_materialization_and_verification_total",
            "total_charged_symmetrically_to_each_arm",
        }
    ):
        raise PrerequisiteError(
            f"{name} frozen dependency-layer provenance is invalid"
        )
    layer_build_ms = _finite_nonnegative(
        layer_timing["build_once"],
        label=f"{name} dependency layer build",
    )
    layer_materialization_total_ms = _finite_nonnegative(
        layer_timing["private_materialization_and_verification_total"],
        label=f"{name} dependency layer materialization total",
    )
    layer_charged_total_ms = _finite_nonnegative(
        layer_timing["total_charged_symmetrically_to_each_arm"],
        label=f"{name} dependency layer charged total",
    )
    recorded_build_ms = _finite_nonnegative(
        dependency_layer.get("build_once_ms"),
        label=f"{name} dependency layer recorded build",
    )
    if (
        not math.isclose(layer_build_ms, recorded_build_ms, abs_tol=0.005)
        or not math.isclose(
            math.fsum((layer_build_ms, layer_materialization_total_ms)),
            layer_charged_total_ms,
            abs_tol=0.005,
        )
        or not math.isclose(
            layer_charged_total_ms,
            costs["symmetric_environment_bootstrap_charged_to_each_arm"],
            abs_tol=0.005,
        )
    ):
        raise PrerequisiteError(
            f"{name} dependency-layer timing does not reconcile to the primary ledger"
        )

    trajectory_records = row.get("trajectory_records")
    adverse_audits = row.get("adverse_audits")
    candidate_digest = row.get("candidate_sha256")
    activation_digest = row.get("activation_candidate_sha256")
    review_digest = row.get("review_record_sha256")
    candidate_count = row.get("candidate_node_count")
    candidate_reviewable = row.get("candidate_reviewable_nodes")
    candidate_fresh = row.get("candidate_fresh_required_nodes")
    if (
        not isinstance(trajectory_records, list)
        or len(trajectory_records) != 2
        or not isinstance(adverse_audits, list)
        or len(adverse_audits) != 2
        or SHA64.fullmatch(str(candidate_digest)) is None
        or activation_digest != candidate_digest
        or SHA64.fullmatch(str(review_digest)) is None
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (candidate_count, candidate_reviewable, candidate_fresh)
        )
        or candidate_reviewable + candidate_fresh != candidate_count
        or row.get("activation_evidence_kind")
        != "mechanical-synthetic-formative-fixture"
        or row.get("external_hmac_authority_created") is not False
    ):
        raise PrerequisiteError(f"{name} lacks both isolated trajectory records")
    trajectory_refresh_counts: dict[int, int] = {1: 0, 2: 0}
    recorded_costs = {
        "symmetric_environment_bootstrap_charged_to_each_arm": layer_build_ms,
        "direct_seed_charged_to_direct": 0.0,
        "zerorun_qualification_charged_to_zerorun": 0.0,
        "zerorun_activation_charged_to_zerorun": 0.0,
        "zerorun_seed_charged_to_zerorun": 0.0,
        "automatic_refresh_charged_to_zerorun": 0.0,
    }
    recorded_materialization_total_ms = 0.0
    for index, record in enumerate(trajectory_records, 1):
        if (
            not isinstance(record, dict)
            or record.get("trajectory") != index
            or record.get("order") != expected_orders[index - 1]
            or record.get("isolated_checkout_and_cache") is not True
            or record.get("transition_count") != 20
            or record.get("profile_refresh_fail_closed_violations") != 0
            or record.get("automatic_refreshes_performed") != 0
            or record.get("candidate_sha256") != candidate_digest
            or record.get("activation_candidate_sha256") != candidate_digest
            or record.get("review_record_sha256") != review_digest
            or record.get("candidate_node_count") != candidate_count
            or record.get("candidate_reviewable_nodes") != candidate_reviewable
            or record.get("candidate_fresh_required_nodes") != candidate_fresh
            or not isinstance(record.get("adverse_audit"), dict)
            or record["adverse_audit"].get("pass") is not True
            or record["adverse_audit"].get("included_in_performance_timing") is not False
            or record["adverse_audit"] != adverse_audits[index - 1]
        ):
            raise PrerequisiteError(f"{name} trajectory {index} is incomplete")
        _validate_comparison_evidence(
            record.get("seed_comparison"),
            label=f"{name} trajectory {index} seed",
            expected_direct_code=0,
            expected_product_code=0,
            expected_total_nodes=candidate_count,
            expected_reused_nodes=0,
        )
        materialization = record.get("environment_materialization")
        if (
            not isinstance(materialization, dict)
            or materialization.get("schema") != PERFORMANCE_MATERIALIZATION_SCHEMA
            or materialization.get("content_tree_sha256") != content_tree
            or materialization.get("construction_identity_sha256")
            != construction_identity
            or materialization.get("manifest_sha256")
            != dependency_layer.get("manifest_sha256")
            or materialization.get("private_byte_copy") is not True
            or materialization.get("shared_hardlinks") is not False
            or materialization.get("read_only_mode_and_content_verified") is not True
            or materialization.get("isolated_result_cache") is not True
        ):
            raise PrerequisiteError(
                f"{name} trajectory {index} dependency materialization is invalid"
            )
        materialization_components = [
            _finite_nonnegative(
                materialization.get(component),
                label=f"{name} trajectory {index} {component}",
            )
            for component in (
                "private_copy_ms",
                "verification_ms",
                "manifest_write_and_verify_ms",
                "timing_remainder_ms",
            )
        ]
        trajectory_environment_ms = _finite_nonnegative(
            record.get("environment_bootstrap_ms"),
            label=f"{name} trajectory {index} environment bootstrap",
        )
        if not math.isclose(
            math.fsum(materialization_components),
            trajectory_environment_ms,
            abs_tol=0.01,
        ):
            raise PrerequisiteError(
                f"{name} trajectory {index} materialization timing does not reconcile"
            )
        recorded_materialization_total_ms += trajectory_environment_ms
        recorded_costs[
            "symmetric_environment_bootstrap_charged_to_each_arm"
        ] += trajectory_environment_ms
        recorded_costs["direct_seed_charged_to_direct"] += _finite_nonnegative(
            record.get("direct_seed_ms"),
            label=f"{name} trajectory {index} direct seed",
        )
        recorded_costs[
            "zerorun_qualification_charged_to_zerorun"
        ] += _finite_nonnegative(
            record.get("qualification_ms"),
            label=f"{name} trajectory {index} qualification",
        )
        recorded_costs[
            "zerorun_activation_charged_to_zerorun"
        ] += _finite_nonnegative(
            record.get("activation_ms"),
            label=f"{name} trajectory {index} activation",
        )
        recorded_costs["zerorun_seed_charged_to_zerorun"] += _finite_nonnegative(
            record.get("zerorun_seed_ms"),
            label=f"{name} trajectory {index} ZeroRun seed",
        )
        declared_refresh_count = record.get(
            "profile_refresh_required_observations"
        )
        if (
            isinstance(declared_refresh_count, bool)
            or not isinstance(declared_refresh_count, int)
            or declared_refresh_count < 0
        ):
            raise PrerequisiteError(
                f"{name} trajectory {index} refresh count is malformed"
            )
    if not math.isclose(
        recorded_materialization_total_ms,
        layer_materialization_total_ms,
        abs_tol=0.005,
    ):
        raise PrerequisiteError(
            f"{name} dependency materialization timings do not reconcile"
        )
    for audit_index, audit in enumerate(adverse_audits):
        cache_before = audit.get("cache_publication_state_before") if isinstance(audit, dict) else None
        cache_after = audit.get("cache_publication_state_after") if isinstance(audit, dict) else None
        valid_cache_state = True
        valid_audit_timings = True
        for state in (cache_before, cache_after):
            if (
                not isinstance(state, dict)
                or state.get("schema") != "zerorun.cache-publication-state.v1"
                or SHA64.fullmatch(str(state.get("sha256"))) is None
                or any(
                    isinstance(state.get(field), bool)
                    or not isinstance(state.get(field), int)
                    or state[field] < 0
                    for field in ("file_count", "directory_count", "total_bytes")
                )
            ):
                valid_cache_state = False
        try:
            for field in (
                "direct_wall_ms",
                "zerorun_wall_ms",
                "independent_shadow_wall_ms",
            ):
                _finite_nonnegative(
                    audit.get(field) if isinstance(audit, dict) else None,
                    label=f"{name} adverse audit {field}",
                )
        except PrerequisiteError:
            valid_audit_timings = False
        if (
            not isinstance(audit, dict)
            or audit.get("status") != "PASS"
            or audit.get("pass") is not True
            or audit.get("kind") != "transient-changed-source-collection-failure"
            or audit.get("target") not in details["targets"]
            or audit.get("order") != expected_orders[audit_index]
            or any(
                isinstance(audit.get(field), bool)
                or not isinstance(audit.get(field), int)
                or audit[field] == 0
                for field in (
                    "direct_exit_code",
                    "zerorun_exit_code",
                    "independent_shadow_exit_code",
                )
            )
            or not valid_audit_timings
            or audit.get("zerorun_stale_success") is not False
            or audit.get("zerorun_status") != "PYTEST_INCREMENTAL_FAIL"
            or audit.get("zerorun_reused_nodes") != 0
            or audit.get("zerorun_published_nodes") != 0
            or audit.get("zerorun_reuse_authorized") is not False
            or audit.get("profile_refresh_required") is not False
            or audit.get("profile_refresh_reasons") != []
            or audit.get("fail_closed_refusal_semantics") is not True
            or not valid_cache_state
            or cache_before != cache_after
            or audit.get("cache_publication_state_unchanged") is not True
            or audit.get("source_restored_to_frozen_bytes") is not True
            or audit.get("included_in_performance_timing") is not False
        ):
            raise PrerequisiteError(f"{name} adverse audit did not pass outside timing")
    for component, observed in recorded_costs.items():
        if not math.isclose(costs[component], observed, rel_tol=0.0, abs_tol=0.002):
            raise PrerequisiteError(
                f"{name} primary cost ledger was not derived from trajectory records"
            )

    rows = row.get("rows")
    if not isinstance(rows, list) or len(rows) != 20:
        raise PrerequisiteError(f"{name} lacks exactly 20 primary transition rows")
    direct_times: list[float] = []
    zerorun_times: list[float] = []
    steady_direct_times: list[float] = []
    steady_zerorun_times: list[float] = []
    refreshed_observations = 0
    total_reused = 0
    total_fresh = 0
    total_unknown = 0
    total_nodes = 0
    total_published = 0
    recovery_runs = 0
    cache_conflicts = 0
    for index, transition in enumerate(rows, 1):
        if not isinstance(transition, dict):
            raise PrerequisiteError(f"{name} transition {index} is malformed")
        direct_ms = _finite_positive(
            transition.get("primary_end_to_end_plain_pytest_ms"),
            label=f"{name} transition {index} direct primary time",
        )
        zerorun_ms = _finite_positive(
            transition.get("primary_end_to_end_zerorun_ms"),
            label=f"{name} transition {index} ZeroRun primary time",
        )
        steady_direct = _finite_positive(
            transition.get("steady_state_plain_pytest_transition_ms"),
            label=f"{name} transition {index} steady direct time",
        )
        steady_zerorun = _finite_positive(
            transition.get("steady_state_zerorun_transition_ms"),
            label=f"{name} transition {index} steady ZeroRun time",
        )
        repetitions = transition.get("counterbalanced_repetitions")
        if (
            transition.get("case") != index
            or transition.get("commit") != corpus[index]
            or transition.get("previous_commit") != corpus[index - 1]
            or transition.get("direct_ms") != transition.get(
                "primary_end_to_end_plain_pytest_ms"
            )
            or transition.get("zerorun_ms") != transition.get(
                "primary_end_to_end_zerorun_ms"
            )
            or transition.get("direct_exit_code") != 0
            or transition.get("zerorun_exit_code") != 0
            or transition.get("direct_runner_independent_of_zerorun_internals") is not True
            or transition.get("direct_collection_separately_measured") is not False
            or transition.get(
                "direct_collection_included_in_uninstrumented_single_pass_wall"
            )
            is not True
            or transition.get("repetition_count_per_arm") != 2
            or transition.get("all_repetition_comparisons_pass") is not True
            or not isinstance(repetitions, list)
            or len(repetitions) != 2
            or {item.get("order") for item in repetitions if isinstance(item, dict)}
            != set(expected_orders)
        ):
            raise PrerequisiteError(f"{name} transition {index} changed the paired design")
        observation_direct_ms: list[float] = []
        observation_zerorun_ms: list[float] = []
        observation_reused = 0
        observation_fresh = 0
        observation_unknown = 0
        observation_nodes = 0
        observation_published = 0
        observation_phase_maps: list[dict[str, float]] = []
        observation_timing_maps: list[dict[str, float]] = []
        observation_statuses: list[str] = []
        for observation in repetitions:
            if not isinstance(observation, dict):
                raise PrerequisiteError(
                    f"{name} transition {index} observation is malformed"
                )
            trajectory = observation.get("trajectory")
            comparison = observation.get("comparison")
            observed_direct = _finite_positive(
                observation.get("direct_transition_wall_ms"),
                label=f"{name} transition {index} observation direct time",
            )
            observed_zerorun = _finite_positive(
                observation.get("zerorun_transition_wall_ms"),
                label=f"{name} transition {index} observation ZeroRun time",
            )
            observed_reused = observation.get("reused_nodes")
            observed_fresh = observation.get("fresh_nodes")
            observed_unknown = observation.get("unknown_nodes")
            observed_nodes = observation.get("total_nodes")
            observed_published = observation.get("published_nodes")
            product_collection = observation.get("zerorun_collection_sha256")
            refresh_required = observation.get("profile_refresh_required")
            refresh_reasons = observation.get("profile_refresh_reasons")
            refresh_fail_closed = bool(
                refresh_required is False
                or (observed_reused == 0 and observed_published == 0)
            )
            if (
                trajectory not in {1, 2}
                or observation.get("order") != expected_orders[trajectory - 1]
                or observation.get("case") != index
                or observation.get("commit") != corpus[index]
                or observation.get("previous_commit") != corpus[index - 1]
                or observation.get("direct_exit_code") != 0
                or observation.get("zerorun_exit_code") != 0
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                    for value in (
                        observed_reused,
                        observed_fresh,
                        observed_unknown,
                        observed_nodes,
                        observed_published,
                    )
                )
                or observed_reused + observed_fresh != observed_nodes
                or observed_unknown > observed_fresh
                or observed_published > observed_fresh
                or SHA64.fullmatch(str(product_collection)) is None
                or not isinstance(refresh_required, bool)
                or not isinstance(refresh_reasons, list)
                or any(not isinstance(reason, str) or not reason for reason in refresh_reasons)
                or observation.get("profile_refresh_fail_closed") is not refresh_fail_closed
                or (
                    refresh_required is True
                    and (
                        observed_reused != 0
                        or observed_published != 0
                        or observation.get("reuse_authorized") is not False
                        or not refresh_reasons
                    )
                )
                or observation.get("reuse_authorized") is not bool(observed_reused)
                or not isinstance(observation.get("zerorun_status"), str)
                or not observation["zerorun_status"]
            ):
                raise PrerequisiteError(
                    f"{name} transition {index} has incomplete fresh-shadow evidence"
                )
            _validate_comparison_evidence(
                comparison,
                label=f"{name} transition {index} trajectory {trajectory}",
                expected_direct_code=0,
                expected_product_code=0,
                expected_product_collection=product_collection,
                expected_total_nodes=observed_nodes,
                expected_reused_nodes=observed_reused,
            )
            direct_phase = _validate_timing_mapping(
                observation.get("direct_phase_ms"),
                label=f"{name} transition {index} direct observation",
                required_keys={
                    "end_to_end_container_wall_including_collection_execution_and_cleanup"
                },
            )
            if not _close(
                direct_phase[
                    "end_to_end_container_wall_including_collection_execution_and_cleanup"
                ],
                observed_direct,
                places=3,
            ):
                raise PrerequisiteError(
                    f"{name} transition {index} direct phase does not reconcile"
                )
            phase_map = _validate_timing_mapping(
                observation.get("zerorun_phase_ms"),
                label=f"{name} transition {index} ZeroRun phase",
            )
            timing_map = _validate_timing_mapping(
                observation.get("zerorun_timing_ms"),
                label=f"{name} transition {index} ZeroRun observation",
                required_keys={
                    "outer_wall",
                    "product_reported_wall",
                    "execution",
                    "collection_overhead",
                    "single_pass_wall",
                    "avoided_execution_reference",
                },
            )
            if (
                not isinstance(observation.get("zerorun_phase_ms_emitted"), bool)
                or not _close(timing_map["outer_wall"], observed_zerorun, places=3)
            ):
                raise PrerequisiteError(
                    f"{name} transition {index} ZeroRun timing does not reconcile"
                )
            refreshed_observations += refresh_required is True
            trajectory_refresh_counts[trajectory] += (
                refresh_required is True
            )
            observation_direct_ms.append(observed_direct)
            observation_zerorun_ms.append(observed_zerorun)
            observation_reused += observed_reused
            observation_fresh += observed_fresh
            observation_unknown += observed_unknown
            observation_nodes += observed_nodes
            observation_published += observed_published
            observation_phase_maps.append(phase_map)
            observation_timing_maps.append(timing_map)
            observation_statuses.append(observation["zerorun_status"])
        product_collections = {
            observation["zerorun_collection_sha256"] for observation in repetitions
        }
        common_collection = (
            next(iter(product_collections)) if len(product_collections) == 1 else None
        )
        if any("CONFLICT" in status for status in observation_statuses):
            expected_status = "COUNTERBALANCED_CONFLICT"
        elif any(status == "PYTEST_FRESH_RECOVERY" for status in observation_statuses):
            expected_status = "PYTEST_FRESH_RECOVERY"
        elif len(set(observation_statuses)) == 1:
            expected_status = observation_statuses[0]
        else:
            expected_status = "COUNTERBALANCED_MIXED_PASS"
        expected_phase_map = _sum_timing_mappings(observation_phase_maps)
        expected_timing_map = _sum_timing_mappings(observation_timing_maps)
        expected_timing_map.update(
            {
                "amortized_environment_qualification_activation_and_seed": round(
                    zerorun_overhead / 20.0, 3
                ),
                "primary_end_to_end_paired_total": round(zerorun_ms, 3),
            }
        )
        direct_phase = transition.get("direct_phase_ms")
        order_range = transition.get("order_pair_range_ms")
        reused = transition.get("reused_nodes")
        fresh = transition.get("fresh_nodes")
        unknown = transition.get("unknown_nodes")
        nodes = transition.get("total_nodes")
        published = transition.get("published_nodes")
        if (
            any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (reused, fresh, unknown, nodes, published))
            or reused + fresh != nodes
            or unknown > fresh
            or published > fresh
            or transition.get("reused_node_fresh_shadow_coverage") != reused
            or transition.get("reused_node_fresh_shadow_denominator") != reused
            or reused != observation_reused
            or fresh != observation_fresh
            or unknown != observation_unknown
            or nodes != observation_nodes
            or published != observation_published
            or transition.get("reuse_authorized") is not bool(reused)
            or transition.get("direct_collection_sha256") != common_collection
            or transition.get("zerorun_collection_sha256") != common_collection
            or SHA64.fullmatch(str(common_collection)) is None
            or transition.get("zerorun_status") != expected_status
            or transition.get("zerorun_phase_ms") != expected_phase_map
            or transition.get("zerorun_phase_ms_emitted")
            is not all(
                observation.get("zerorun_phase_ms_emitted") is True
                for observation in repetitions
            )
            or transition.get("zerorun_timing_ms") != expected_timing_map
            or not isinstance(direct_phase, dict)
            or set(direct_phase)
            != {
                "timed_transition_outer_wall_sum",
                "amortized_environment_and_direct_seed",
                "primary_end_to_end_paired_total",
            }
            or not _close(
                _finite_nonnegative(
                    direct_phase.get("timed_transition_outer_wall_sum"),
                    label=f"{name} transition {index} direct phase wall",
                ),
                steady_direct,
                places=3,
            )
            or not math.isclose(
                _finite_nonnegative(
                    direct_phase.get("amortized_environment_and_direct_seed"),
                    label=f"{name} transition {index} direct amortization",
                ),
                direct_overhead / 20.0,
                rel_tol=0.0,
                abs_tol=0.002,
            )
            or not _close(
                _finite_positive(
                    direct_phase.get("primary_end_to_end_paired_total"),
                    label=f"{name} transition {index} direct primary phase",
                ),
                direct_ms,
                places=3,
            )
            or not isinstance(order_range, dict)
            or set(order_range) != {"plain_pytest", "zerorun"}
            or not _close(
                _finite_nonnegative(
                    order_range.get("plain_pytest"),
                    label=f"{name} transition {index} plain order range",
                ),
                max(observation_direct_ms) - min(observation_direct_ms),
                places=3,
            )
            or not _close(
                _finite_nonnegative(
                    order_range.get("zerorun"),
                    label=f"{name} transition {index} ZeroRun order range",
                ),
                max(observation_zerorun_ms) - min(observation_zerorun_ms),
                places=3,
            )
            or not _close_rounded_ms(steady_direct, math.fsum(observation_direct_ms))
            or not _close_rounded_ms(
                steady_zerorun, math.fsum(observation_zerorun_ms)
            )
            or not _close_rounded_ms(
                direct_ms,
                steady_direct + direct_overhead / 20.0,
            )
            or not _close_rounded_ms(
                zerorun_ms,
                steady_zerorun + zerorun_overhead / 20.0,
            )
        ):
            raise PrerequisiteError(f"{name} transition {index} node counts do not reconcile")
        direct_times.append(direct_ms)
        zerorun_times.append(zerorun_ms)
        steady_direct_times.append(steady_direct)
        steady_zerorun_times.append(steady_zerorun)
        total_reused += reused
        total_fresh += fresh
        total_unknown += unknown
        total_nodes += nodes
        total_published += published
        recovery_runs += expected_status == "PYTEST_FRESH_RECOVERY"
        cache_conflicts += "CONFLICT" in expected_status

    if profile_refresh["required_observations"] != refreshed_observations:
        raise PrerequisiteError(f"{name} profile refresh count does not reconcile")
    for trajectory, count in trajectory_refresh_counts.items():
        if (
            trajectory_records[trajectory - 1].get(
                "profile_refresh_required_observations"
            )
            != count
        ):
            raise PrerequisiteError(
                f"{name} trajectory {trajectory} refresh count does not reconcile"
            )
    direct_total = math.fsum(direct_times)
    zerorun_total = math.fsum(zerorun_times)
    steady_direct_total = math.fsum(steady_direct_times)
    steady_zerorun_total = math.fsum(steady_zerorun_times)
    reported_direct_total = _finite_positive(
        row.get("direct_total_ms"), label=f"{name} direct total"
    )
    reported_zerorun_total = _finite_positive(
        row.get("zerorun_total_ms"), label=f"{name} ZeroRun total"
    )
    reported_steady_direct = _finite_positive(
        row.get("steady_state_plain_pytest_total_ms"),
        label=f"{name} steady direct total",
    )
    reported_steady_zerorun = _finite_positive(
        row.get("steady_state_zerorun_total_ms"),
        label=f"{name} steady ZeroRun total",
    )
    if (
        not _close(reported_direct_total, direct_total, places=3)
        or not _close(reported_zerorun_total, zerorun_total, places=3)
        or not _close(reported_steady_direct, steady_direct_total, places=3)
        or not _close(reported_steady_zerorun, steady_zerorun_total, places=3)
        or not math.isclose(
            direct_total,
            steady_direct_total + direct_overhead,
            rel_tol=0.0,
            abs_tol=0.02,
        )
        or not math.isclose(
            zerorun_total,
            steady_zerorun_total + zerorun_overhead,
            rel_tol=0.0,
            abs_tol=0.02,
        )
    ):
        raise PrerequisiteError(f"{name} primary totals do not reconcile from raw rows")

    recomputed_speedup = direct_total / zerorun_total
    compute = _finite_positive(
        row.get("end_to_end_plain_pytest_to_zerorun_speedup"),
        label=f"{name} end-to-end speedup",
    )
    alias_compute = _finite_positive(
        row.get("same_runner_compute_efficiency"),
        label=f"{name} compatibility speedup alias",
    )
    direct_p95 = _nearest_rank(direct_times, 0.95)
    zerorun_p95 = _nearest_rank(zerorun_times, 0.95)
    recomputed_p95_reduction = (1.0 - zerorun_p95 / direct_p95) * 100.0
    p95 = _finite_number(
        row.get("end_to_end_p95_reduction_percent"),
        label=f"{name} end-to-end p95 reduction",
    )
    alias_p95 = _finite_number(
        row.get("p95_reduction_percent"), label=f"{name} p95 compatibility alias"
    )
    if (
        compute != alias_compute
        or not _close(compute, recomputed_speedup, places=6)
        or p95 != alias_p95
        or not _close(p95, recomputed_p95_reduction, places=3)
        or not _close(
            _finite_positive(row.get("direct_p95_ms"), label=f"{name} direct p95"),
            direct_p95,
            places=3,
        )
        or not _close(
            _finite_positive(row.get("zerorun_p95_ms"), label=f"{name} ZeroRun p95"),
            zerorun_p95,
            places=3,
        )
        or compute < REFERENCE_GATE["min_compute_efficiency"]
        or p95 < REFERENCE_GATE["min_p95_reduction_percent"]
    ):
        raise PrerequisiteError(f"{name} did not independently pass the 5x/50 safety gate")

    p95_estimation = row.get("p95_estimation")
    if (
        not isinstance(p95_estimation, dict)
        or p95_estimation.get("estimator")
        != "nearest-rank-on-counterbalanced-paired-transition-totals"
        or p95_estimation.get("sample_size") != 20
        or p95_estimation.get("rank") != 19
        or p95_estimation.get("independent_repository_count") != 1
        or p95_estimation.get("repetitions_per_transition_per_arm") != 2
        or p95_estimation.get("confidence_interval_reported") is not False
    ):
        raise PrerequisiteError(f"{name} p95 estimator disclosure is invalid")

    if (
        row.get("reused_nodes") != total_reused
        or row.get("fresh_nodes") != total_fresh
        or row.get("unknown_nodes") != total_unknown
        or row.get("total_nodes") != total_nodes
        or row.get("published_nodes") != total_published
        or row.get("recovery_runs") != recovery_runs
        or row.get("stale_successes") != 0
        or row.get("shadow_mismatches") != 0
        or row.get("cache_conflicts") != cache_conflicts
        or cache_conflicts != 0
        or row.get("direct_failures") != 0
        or row.get("zerorun_failures") != 0
        or not _close(
            _finite_nonnegative(
                row.get("reuse_rate_percent"), label=f"{name} reuse rate"
            ),
            (total_reused / total_nodes * 100.0) if total_nodes else 0.0,
            places=3,
        )
    ):
        raise PrerequisiteError(f"{name} 5x/50 safety totals do not reconcile")

    _validate_source_identity(
        row.get("source_identity"),
        candidate_sha=candidate_sha,
        label=name,
        root=root,
    )
    return {
        "workload": name,
        "end_to_end_plain_pytest_to_zerorun_speedup": compute,
        "end_to_end_p95_reduction_percent": p95,
        "same_runner_compute_efficiency": alias_compute,
        "p95_reduction_percent": alias_p95,
    }


def _validate_performance(
    receipt: Mapping[str, Any], *, candidate_sha: str, root: Path
) -> dict[str, Any]:
    if receipt.get("schema") != PERFORMANCE_SCHEMA:
        raise PrerequisiteError("performance receipt schema is unsupported")
    if (
        receipt.get("activation_evidence_kind")
        != PERFORMANCE_ACTIVATION_EVIDENCE_KIND
        or receipt.get("external_hmac_authority_created") is not False
        or receipt.get("protocol") != PERFORMANCE_PROTOCOL_DISCLOSURE
        or receipt.get("release_gate") != PERFORMANCE_RELEASE_GATE_DISCLOSURE
    ):
        raise PrerequisiteError(
            "aggregate performance receipt changed its synthetic/formative "
            "evidence disclosure"
        )
    if (
        receipt.get("engine_sha") != candidate_sha
        or receipt.get("reference_gate") != REFERENCE_GATE
        or receipt.get("all_safety_pass") is not True
        or receipt.get("old_holdout_regression_pass") is not True
        or receipt.get("new_unseen_generalization_pass") is not True
        or receipt.get("general_5x_claim_supported_by_this_suite") is not True
    ):
        raise PrerequisiteError("aggregate five-repository performance gate did not pass")
    runtime_lock = hashlib.sha256(
        (root / "ci" / "generalization-runtime-requirements.txt").read_bytes()
    ).hexdigest()
    if receipt.get("runtime_requirements_sha256") != runtime_lock:
        raise PrerequisiteError("performance runtime lock is not the release candidate lock")
    workloads = receipt.get("workloads")
    if not isinstance(workloads, list) or len(workloads) != len(EXPECTED_WORKLOADS):
        raise PrerequisiteError("performance receipt does not contain exactly five workloads")
    by_name: dict[str, Mapping[str, Any]] = {}
    summary: list[dict[str, Any]] = []
    for index, row in enumerate(workloads):
        if not isinstance(row, dict) or not isinstance(row.get("workload"), str):
            raise PrerequisiteError(f"performance workload {index} is malformed")
        name = row["workload"]
        if name in by_name:
            raise PrerequisiteError(f"performance workload {name} is duplicated")
        by_name[name] = row
    if set(by_name) != set(EXPECTED_WORKLOADS):
        raise PrerequisiteError("performance workload identities changed")
    for name, holdout_class in EXPECTED_WORKLOADS.items():
        row = by_name[name]
        summary.append(
            _validate_performance_workload(
                row,
                name=name,
                holdout_class=holdout_class,
                candidate_sha=candidate_sha,
                runtime_lock=runtime_lock,
                root=root,
            )
        )
    return {"reference_gate": dict(REFERENCE_GATE), "workloads": summary}


def _validate_commercial(
    receipt: Mapping[str, Any],
    *,
    candidate_sha: str,
    version: str,
    selection: Mapping[str, Any],
    repositories: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if receipt.get("schema") != COMMERCIAL_SCHEMA:
        raise PrerequisiteError("commercial receipt schema is unsupported")
    counts = receipt.get("counts")
    if not isinstance(counts, dict) or set(counts) != {"pass", "safe_refusal", "failure"}:
        raise PrerequisiteError("commercial outcome counts are malformed")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
        raise PrerequisiteError("commercial outcome counts are malformed")
    if (
        receipt.get("engine_commit") != candidate_sha
        or receipt.get("engine_version") != version
        or receipt.get("codex_version") != EXPECTED_CODEX_REPORTED_VERSION
        or receipt.get("selection_sha256") != selection.get("corpus_sha256")
        or receipt.get("upstream_code_executed") is not False
        or receipt.get("attempted_repositories") != 100
        or receipt.get("distinct_repositories") != 100
        or receipt.get("minimum_successful_integrations") != 90
        or receipt.get("legacy_observe_test_rejections") != counts["pass"]
        or receipt.get("gate_pass") is not True
        or counts["failure"] != 0
        or counts["pass"] < 90
        or sum(counts.values()) != 100
    ):
        raise PrerequisiteError("commercial 100-repository gate did not pass")
    rows = receipt.get("rows")
    if not isinstance(rows, list) or len(rows) != 100:
        raise PrerequisiteError("commercial receipt lacks the exact attempted denominator")
    observed_counts = {key: 0 for key in counts}
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise PrerequisiteError("commercial receipt contains a malformed row")
        index = row.get("selection_index")
        status = row.get("status")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= 100
            or index in seen
            or status not in observed_counts
        ):
            raise PrerequisiteError("commercial receipt contains an invalid selection outcome")
        expected = repositories[index]
        if (row.get("repository"), row.get("commit")) != (
            expected.get("repository"),
            expected.get("commit"),
        ):
            raise PrerequisiteError(f"commercial identity mismatch at selection index {index}")
        if row.get("phase") != "complete":
            raise PrerequisiteError(f"commercial selection index {index} is incomplete")
        if status == "pass":
            install = row.get("install")
            mcp = row.get("mcp")
            if (
                row.get("installed_skill_sha256")
                != EXPECTED_MANAGED_SKILL_SHA256
                or not isinstance(install, dict)
                or install.get("schema") != "zerorun-codex-install-v5"
                or install.get("integration_ready") is not True
                or not isinstance(install.get("skill"), dict)
                or install["skill"].get("installed") is not True
                or install["skill"].get("conflict") is not False
                or not isinstance(install.get("mcp"), dict)
                or install["mcp"].get("registered") is not True
                or not isinstance(mcp, dict)
                or mcp.get("server") != {"name": "zerorun", "version": version}
                or mcp.get("tool_count") != 7
                or mcp.get("escape_rejected") is not True
                or mcp.get("legacy_observe_test_rejected") is not True
                or mcp.get("doctor_mode")
                not in {"observe-only", "task-reuse", "pytest-node-reuse"}
                or mcp.get("stats_mode")
                not in {"observe-only", "task-reuse", "pytest-node-reuse"}
            ):
                raise PrerequisiteError(
                    f"commercial pass {index} lacks complete Codex/MCP proof"
                )
        elif status == "safe_refusal":
            install = row.get("install")
            proof = row.get("refusal_proof")
            if (
                not isinstance(row.get("reason"), str)
                or not row["reason"]
                or not isinstance(install, dict)
                or install.get("integration_ready") is not False
                or not isinstance(install.get("skill"), dict)
                or install["skill"].get("conflict") is not True
                or install["skill"].get("installed") is not False
                or not isinstance(proof, dict)
                or proof.get("skill_conflict") is not True
                or proof.get("unchanged") is not True
                or proof.get("before") != proof.get("after")
                or not isinstance(proof.get("before"), dict)
                or proof["before"].get("kind")
                not in {"link", "regular_file", "non_regular"}
            ):
                raise PrerequisiteError(f"commercial safe refusal {index} lacks proof")
        seen.add(index)
        observed_counts[status] += 1
    if seen != set(range(100)) or observed_counts != counts:
        raise PrerequisiteError("commercial rows do not reproduce the aggregate denominator")
    return {"counts": dict(counts), "attempted_repositories": 100}


def _package_source_bytes(root: Path) -> dict[str, bytes]:
    sources: dict[str, bytes] = {}
    for path in sorted(
        (root / "zerorun").rglob("*.py"),
        key=lambda item: (
            item.relative_to(root).as_posix().casefold(),
            item.relative_to(root).as_posix(),
        ),
    ):
        if path.is_symlink() or not path.is_file():
            raise PrerequisiteError("release source inventory contains an unsafe Python file")
        sources[path.relative_to(root).as_posix()] = path.read_bytes()
    if not sources:
        raise PrerequisiteError("release source inventory is empty")
    return sources


def _safe_archive_name(value: str, *, label: str) -> None:
    path = Path(value.replace("/", os.sep))
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise PrerequisiteError(f"{label} contains an unsafe archive path")


def _metadata_identity(raw: bytes, *, label: str, version: str) -> None:
    if len(raw) > 1024 * 1024:
        raise PrerequisiteError(f"{label} metadata is oversized")
    try:
        metadata = BytesParser().parsebytes(raw, headersonly=True)
    except Exception as exc:
        raise PrerequisiteError(f"{label} metadata is malformed") from exc
    if (
        metadata.get("Name") != "zerorun"
        or metadata.get("Version") != version
        or metadata.get("Summary") != EXPECTED_PACKAGE_SUMMARY
        or metadata.get("Requires-Python") != EXPECTED_REQUIRES_PYTHON
        or metadata.get("License-Expression") != EXPECTED_LICENSE_EXPRESSION
        or metadata.get_all("License-File", []) != ["LICENSE.txt"]
        or metadata.get_all("Requires-Dist", [])
    ):
        raise PrerequisiteError(f"{label} package metadata contract is incorrect")


def _validate_entry_points(raw: bytes, *, label: str) -> None:
    try:
        lines = tuple(raw.decode("utf-8", errors="strict").splitlines())
    except UnicodeDecodeError as exc:
        raise PrerequisiteError(f"{label} entry points are not UTF-8") from exc
    if lines != EXPECTED_CONSOLE_SCRIPTS:
        raise PrerequisiteError(f"{label} entry points differ from the reviewed contract")


def _checkout_python_bytes(root: Path, directory: str) -> dict[str, bytes]:
    base = root / directory
    result: dict[str, bytes] = {}
    for path in sorted(
        base.rglob("*.py"),
        key=lambda item: (
            item.relative_to(root).as_posix().casefold(),
            item.relative_to(root).as_posix(),
        ),
    ):
        if path.is_symlink() or not path.is_file():
            raise PrerequisiteError(f"release {directory} inventory contains an unsafe file")
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _validate_attested_file_record(
    value: object,
    *,
    label: str,
    expected_name: str | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"name", "size", "sha256"}:
        raise PrerequisiteError(f"{label} file record is malformed")
    name = value.get("name")
    size = value.get("size")
    digest = value.get("sha256")
    if (
        not isinstance(name, str)
        or not name
        or len(name.encode("utf-8")) > 255
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or (expected_name is not None and name != expected_name)
        or type(size) is not int
        or size <= 0
        or size > MAX_ATTESTED_FILE_BYTES
        or not isinstance(digest, str)
        or SHA64.fullmatch(digest) is None
    ):
        raise PrerequisiteError(f"{label} file record is malformed")
    return {"name": name, "size": size, "sha256": digest}


def _validate_package_build_provenance(
    value: object,
    *,
    root: Path,
    epoch: int,
) -> dict[str, object]:
    label = "package build provenance"
    expected_fields = {
        "source_archive",
        "python",
        "host",
        "build_tools",
        "lockfiles",
        "environment",
        "independent_source_directories",
        "byte_identical_builds",
        "identity_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise PrerequisiteError(f"{label} is missing or malformed")

    identity = value.get("identity_sha256")
    identity_payload = dict(value)
    identity_payload.pop("identity_sha256", None)
    if (
        not isinstance(identity, str)
        or SHA64.fullmatch(identity) is None
        or identity != hashlib.sha256(_canonical(identity_payload)).hexdigest()
    ):
        raise PrerequisiteError(f"{label} identity is invalid")

    source_archive = _validate_attested_file_record(
        value.get("source_archive"),
        label=f"{label} source archive",
        expected_name="source.tar",
    )

    python = value.get("python")
    if not isinstance(python, dict) or set(python) != {
        "implementation",
        "version",
        "cache_tag",
        "executable",
    }:
        raise PrerequisiteError(f"{label} Python identity is malformed")
    if any(python.get(key) != expected for key, expected in EXPECTED_PACKAGE_BUILD_PYTHON.items()):
        raise PrerequisiteError(f"{label} Python identity is not the pinned build runtime")
    executable = _validate_attested_file_record(
        python.get("executable"), label=f"{label} Python executable"
    )

    host = value.get("host")
    if not isinstance(host, dict) or set(host) != {
        "system",
        "release",
        "machine",
        "architecture",
    }:
        raise PrerequisiteError(f"{label} host identity is malformed")
    release_name = host.get("release")
    if (
        host.get("system") != "Linux"
        or host.get("machine") != "x86_64"
        or host.get("architecture") != "64bit"
        or not isinstance(release_name, str)
        or not release_name
        or len(release_name) > 256
    ):
        raise PrerequisiteError(f"{label} host identity is not the pinned runner class")

    if value.get("build_tools") != EXPECTED_PACKAGE_BUILD_TOOLS:
        raise PrerequisiteError(f"{label} build tools are not exactly pinned")
    environment = value.get("environment")
    if environment != {
        "SOURCE_DATE_EPOCH": str(epoch),
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
        "build_isolation": False,
    }:
        raise PrerequisiteError(f"{label} deterministic environment is invalid")
    if (
        value.get("independent_source_directories") != 2
        or value.get("byte_identical_builds") != 2
    ):
        raise PrerequisiteError(f"{label} does not attest two independent identical builds")

    lockfiles = value.get("lockfiles")
    expected_paths = sorted((root / "ci").glob("release-*.txt"))
    if not isinstance(lockfiles, list) or len(lockfiles) != len(expected_paths):
        raise PrerequisiteError(f"{label} lockfile inventory is incomplete")
    for row, path in zip(lockfiles, expected_paths):
        if not isinstance(row, dict) or set(row) != {"path", "name", "size", "sha256"}:
            raise PrerequisiteError(f"{label} lockfile record is malformed")
        relative = path.relative_to(root).as_posix()
        raw = _read_regular_bytes(
            path,
            label=f"release build lockfile {relative}",
            max_bytes=1024 * 1024,
        )
        expected_record = {
            "path": relative,
            "name": path.name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if row != expected_record:
            raise PrerequisiteError(f"{label} lockfile inventory differs from the exact checkout")

    return {
        "identity_sha256": identity,
        "source_archive": source_archive,
        "python_executable": executable,
        "build_tools": dict(EXPECTED_PACKAGE_BUILD_TOOLS),
        "lockfile_count": len(expected_paths),
        "byte_identical_builds": 2,
    }


def _expected_installed_semantic_equivalence(
    *,
    expected_sources: Mapping[str, bytes],
    version: str,
) -> dict[str, object]:
    prefix = "zerorun/"
    module_rows = [
        {
            "path": name[len(prefix) :],
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for name, raw in sorted(expected_sources.items())
    ]
    metadata = {
        "Name": ["zerorun"],
        "Version": [version],
        "Summary": [EXPECTED_PACKAGE_SUMMARY],
        "Requires-Python": [EXPECTED_REQUIRES_PYTHON],
        "License-Expression": [EXPECTED_LICENSE_EXPRESSION],
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
        "version": version,
        "module_tree": module_rows,
        "module_tree_sha256": hashlib.sha256(_canonical(module_rows)).hexdigest(),
        "metadata": metadata,
        "metadata_sha256": hashlib.sha256(_canonical(metadata)).hexdigest(),
        "entry_points": entry_points,
        "entry_points_sha256": hashlib.sha256(_canonical(entry_points)).hexdigest(),
    }
    result: dict[str, object] = {
        "schema": "zerorun.package-install-semantic-equivalence.v1",
        "wheel_vs_sdist_exact": True,
        "module_source_bytes_exact": True,
        "metadata_semantics_exact": True,
        "entry_points_exact": True,
        "semantic_sha256": hashlib.sha256(_canonical(semantic)).hexdigest(),
        "module_tree_sha256": semantic["module_tree_sha256"],
        "metadata_sha256": semantic["metadata_sha256"],
        "entry_points_sha256": semantic["entry_points_sha256"],
        "module_file_count": len(module_rows),
    }
    result["identity_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _validate_installed_semantic_equivalence(
    value: object,
    *,
    expected_sources: Mapping[str, bytes],
    version: str,
) -> dict[str, object]:
    expected = _expected_installed_semantic_equivalence(
        expected_sources=expected_sources,
        version=version,
    )
    if not isinstance(value, dict) or value != expected:
        raise PrerequisiteError(
            "package installed semantic equivalence differs from the exact wheel/sdist contract"
        )
    return {
        "identity_sha256": expected["identity_sha256"],
        "semantic_sha256": expected["semantic_sha256"],
        "module_file_count": expected["module_file_count"],
        "wheel_vs_sdist_exact": True,
    }


def _validate_wheel_contents(
    wheel: Path, *, root: Path, version: str, expected_sources: Mapping[str, bytes]
) -> dict[str, Any]:
    if wheel.stat().st_size <= 0 or wheel.stat().st_size > MAX_WHEEL_BYTES:
        raise PrerequisiteError("candidate wheel size is outside the safety boundary")
    try:
        with zipfile.ZipFile(wheel, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_WHEEL_MEMBERS:
                raise PrerequisiteError("candidate wheel member count is invalid")
            names: set[str] = set()
            files: dict[str, bytes] = {}
            total_unpacked = 0
            for info in infos:
                _safe_archive_name(info.filename.rstrip("/"), label="candidate wheel")
                if info.filename in names:
                    raise PrerequisiteError("candidate wheel contains duplicate members")
                names.add(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode)
                if (
                    info.flag_bits & 0x1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or (file_type not in {0, stat.S_IFREG, stat.S_IFDIR})
                ):
                    raise PrerequisiteError("candidate wheel contains an unsafe member")
                if info.is_dir():
                    continue
                if info.file_size < 0 or info.file_size > 16 * 1024 * 1024:
                    raise PrerequisiteError("candidate wheel member is oversized")
                total_unpacked += info.file_size
                if total_unpacked > MAX_WHEEL_UNPACKED_BYTES:
                    raise PrerequisiteError("candidate wheel payload is oversized")
                raw = archive.read(info)
                if len(raw) != info.file_size:
                    raise PrerequisiteError("candidate wheel member length mismatch")
                files[info.filename] = raw
    except PrerequisiteError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PrerequisiteError("candidate wheel is not a valid safe ZIP archive") from exc

    dist_info = f"zerorun-{version}.dist-info"
    metadata_name = f"{dist_info}/METADATA"
    wheel_name = f"{dist_info}/WHEEL"
    record_name = f"{dist_info}/RECORD"
    entry_points_name = f"{dist_info}/entry_points.txt"
    top_level_name = f"{dist_info}/top_level.txt"
    license_name = f"{dist_info}/licenses/LICENSE.txt"
    expected_members = set(expected_sources) | {
        metadata_name,
        wheel_name,
        record_name,
        entry_points_name,
        top_level_name,
        license_name,
    }
    if set(files) != expected_members or len(infos) != len(expected_members):
        raise PrerequisiteError(
            "candidate wheel member manifest differs from the reviewed exact allowlist"
        )
    _metadata_identity(files[metadata_name], label="wheel", version=version)
    try:
        wheel_metadata = BytesParser().parsebytes(files[wheel_name], headersonly=True)
    except Exception as exc:
        raise PrerequisiteError("candidate wheel WHEEL metadata is malformed") from exc
    if (
        wheel_metadata.get("Wheel-Version") != "1.0"
        or wheel_metadata.get("Generator") != EXPECTED_BUILD_GENERATOR
        or wheel_metadata.get("Root-Is-Purelib") != "true"
        or wheel_metadata.get_all("Tag", []) != ["py3-none-any"]
    ):
        raise PrerequisiteError("candidate wheel has the wrong wheel contract")
    _validate_entry_points(files[entry_points_name], label="wheel")
    if files[top_level_name] != b"zerorun\n":
        raise PrerequisiteError("candidate wheel top-level package is incorrect")
    if files[license_name] != (root / "LICENSE.txt").read_bytes():
        raise PrerequisiteError("candidate wheel license differs from the exact checkout")

    try:
        record_rows = list(
            csv.reader(io.StringIO(files[record_name].decode("utf-8", errors="strict")))
        )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise PrerequisiteError("candidate wheel RECORD is malformed") from exc
    records: dict[str, tuple[str, str]] = {}
    for record in record_rows:
        if len(record) != 3 or record[0] in records:
            raise PrerequisiteError("candidate wheel RECORD is malformed")
        _safe_archive_name(record[0], label="candidate wheel RECORD")
        records[record[0]] = (record[1], record[2])
    if set(records) != set(files):
        raise PrerequisiteError("candidate wheel RECORD does not cover exact file members")
    for name, raw in files.items():
        digest_field, size_field = records[name]
        if name == record_name:
            if digest_field or size_field:
                raise PrerequisiteError("candidate wheel RECORD must leave its own hash empty")
            continue
        expected_digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
        if digest_field != f"sha256={expected_digest}" or size_field != str(len(raw)):
            raise PrerequisiteError(f"candidate wheel RECORD mismatch: {name}")

    wheel_sources = {
        name: raw for name, raw in files.items() if name.startswith("zerorun/") and name.endswith(".py")
    }
    if wheel_sources != dict(expected_sources):
        raise PrerequisiteError("candidate wheel Python sources differ from the exact checkout")
    return {
        "member_count": len(infos),
        "unpacked_bytes": total_unpacked,
        "python_source_count": len(wheel_sources),
    }


def _validate_sdist_contents(
    sdist: Path,
    *,
    root: Path,
    version: str,
    epoch: int,
    expected_sources: Mapping[str, bytes],
) -> dict[str, Any]:
    try:
        from tools.normalize_sdist import SdistNormalizationError, _read_members

        archive_root = f"zerorun-{version}"
        members = _read_members(
            sdist,
            expected_root=archive_root,
            canonical_epoch=epoch,
        )
    except (ImportError, OSError) as exc:
        raise PrerequisiteError("could not load the canonical sdist verifier") from exc
    except SdistNormalizationError as exc:
        raise PrerequisiteError(f"candidate sdist is not canonical: {exc}") from exc
    files = {
        member.name: member.data
        for member in members
        if member.kind == "file"
    }
    metadata_name = f"{archive_root}/PKG-INFO"
    _metadata_identity(files[metadata_name], label="sdist", version=version)
    for name in ("pyproject.toml", "README.md", "LICENSE.txt", "MANIFEST.in"):
        archive_name = f"{archive_root}/{name}"
        source_path = root / name
        if files.get(archive_name) != source_path.read_bytes():
            raise PrerequisiteError(f"candidate sdist {name} differs from the exact checkout")
    sdist_sources = {
        name.removeprefix(archive_root + "/"): raw
        for name, raw in files.items()
        if name.startswith(f"{archive_root}/zerorun/") and name.endswith(".py")
    }
    if sdist_sources != dict(expected_sources):
        raise PrerequisiteError("candidate sdist Python sources differ from the exact checkout")
    expected_tests = _checkout_python_bytes(root, "tests")
    sdist_tests = {
        name.removeprefix(archive_root + "/"): raw
        for name, raw in files.items()
        if name.startswith(f"{archive_root}/tests/") and name.endswith(".py")
    }
    if sdist_tests != expected_tests:
        raise PrerequisiteError("candidate sdist tests differ from the exact checkout")

    egg_info = f"{archive_root}/zerorun.egg-info"
    egg_pkg_info = f"{egg_info}/PKG-INFO"
    egg_sources = f"{egg_info}/SOURCES.txt"
    egg_dependencies = f"{egg_info}/dependency_links.txt"
    egg_entry_points = f"{egg_info}/entry_points.txt"
    egg_top_level = f"{egg_info}/top_level.txt"
    allowed_files = {
        metadata_name,
        f"{archive_root}/pyproject.toml",
        f"{archive_root}/README.md",
        f"{archive_root}/LICENSE.txt",
        f"{archive_root}/MANIFEST.in",
        f"{archive_root}/setup.cfg",
        egg_pkg_info,
        egg_sources,
        egg_dependencies,
        egg_entry_points,
        egg_top_level,
        *(f"{archive_root}/{name}" for name in expected_sources),
        *(f"{archive_root}/{name}" for name in expected_tests),
    }
    expected_directories = {archive_root}
    for name in allowed_files:
        parent = Path(name).parent.as_posix()
        while parent != ".":
            expected_directories.add(parent)
            if parent == archive_root:
                break
            parent = Path(parent).parent.as_posix()
    actual_directories = {
        member.name for member in members if member.kind == "directory"
    }
    if set(files) != allowed_files or actual_directories != expected_directories:
        raise PrerequisiteError(
            "candidate sdist member manifest differs from the reviewed exact allowlist"
        )
    if any(
        member.mode != (0o755 if member.kind == "directory" else 0o644)
        for member in members
    ):
        raise PrerequisiteError("candidate sdist member permissions are not canonical")
    if files[egg_pkg_info] != files[metadata_name]:
        raise PrerequisiteError("candidate sdist metadata copies differ")
    _validate_entry_points(files[egg_entry_points], label="sdist")
    if files[egg_dependencies] != b"\n" or files[egg_top_level] != b"zerorun\n":
        raise PrerequisiteError("candidate sdist generated metadata is incorrect")
    setup_cfg = files[f"{archive_root}/setup.cfg"].replace(b"\r\n", b"\n")
    if setup_cfg != b"[egg_info]\ntag_build = \ntag_date = 0\n\n":
        raise PrerequisiteError("candidate sdist setup.cfg is not the canonical backend output")
    try:
        source_manifest = files[egg_sources].decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise PrerequisiteError("candidate sdist SOURCES.txt is not UTF-8") from exc
    expected_manifest = {
        "LICENSE.txt",
        "MANIFEST.in",
        "README.md",
        "pyproject.toml",
        *(name for name in expected_sources),
        *(name for name in expected_tests),
        "zerorun.egg-info/PKG-INFO",
        "zerorun.egg-info/SOURCES.txt",
        "zerorun.egg-info/dependency_links.txt",
        "zerorun.egg-info/entry_points.txt",
        "zerorun.egg-info/top_level.txt",
    }
    if len(source_manifest) != len(set(source_manifest)) or set(source_manifest) != expected_manifest:
        raise PrerequisiteError("candidate sdist SOURCES.txt is not exact")
    return {
        "member_count": len(members),
        "python_source_count": len(sdist_sources),
    }


def _validate_package(
    candidate_dir: Path,
    *,
    root: Path,
    candidate_sha: str,
    version: str,
    expected_source_date_epoch: int | None = None,
) -> dict[str, Any]:
    if candidate_dir.is_symlink() or not candidate_dir.is_dir():
        raise PrerequisiteError("release candidate directory is invalid")
    entries = sorted(candidate_dir.iterdir(), key=lambda path: path.name)
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise PrerequisiteError("release candidate contains a link or non-file entry")
    wheels = [path for path in entries if path.suffix == ".whl"]
    sdists = [path for path in entries if path.name.endswith(".tar.gz")]
    receipt_path = candidate_dir / "package-receipt.json"
    if (
        len(entries) != 3
        or len(wheels) != 1
        or len(sdists) != 1
        or receipt_path not in entries
        or wheels[0].name != f"zerorun-{version}-py3-none-any.whl"
        or sdists[0].name != f"zerorun-{version}.tar.gz"
    ):
        raise PrerequisiteError("release candidate must contain one exact wheel, sdist, and receipt")
    receipt = _load_object(receipt_path, label="package receipt")
    if set(receipt) != {
        "schema",
        "commit",
        "source_date_epoch",
        "artifacts",
        "build_provenance",
        "installed_semantic_equivalence",
    }:
        raise PrerequisiteError("package receipt fields differ from the reviewed release contract")
    epoch = receipt.get("source_date_epoch")
    if (
        receipt.get("schema") != "zerorun.release-package.v1"
        or receipt.get("commit") != candidate_sha
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch <= 0
        or (
            expected_source_date_epoch is not None
            and epoch != expected_source_date_epoch
        )
    ):
        raise PrerequisiteError("package receipt is not bound to the release candidate")
    recorded = receipt.get("artifacts")
    if not isinstance(recorded, list) or len(recorded) != 2:
        raise PrerequisiteError("package receipt must identify exactly two artifacts")
    by_name: dict[str, Mapping[str, Any]] = {}
    for row in recorded:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise PrerequisiteError("package artifact record is malformed")
        if row["name"] in by_name:
            raise PrerequisiteError("package artifact record is duplicated")
        by_name[row["name"]] = row
    if set(by_name) != {wheels[0].name, sdists[0].name}:
        raise PrerequisiteError("package receipt names do not match candidate files")
    result: list[dict[str, Any]] = []
    for path in (wheels[0], sdists[0]):
        row = by_name[path.name]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if (
            row.get("size") != path.stat().st_size
            or row.get("sha256") != digest
            or SHA64.fullmatch(str(row.get("sha256"))) is None
        ):
            raise PrerequisiteError(f"package artifact digest mismatch: {path.name}")
        result.append({"name": path.name, "size": path.stat().st_size, "sha256": digest})
    expected_sources = _package_source_bytes(root)
    archive_validation = {
        "wheel": _validate_wheel_contents(
            wheels[0],
            root=root,
            version=version,
            expected_sources=expected_sources,
        ),
        "sdist": _validate_sdist_contents(
            sdists[0],
            root=root,
            version=version,
            epoch=epoch,
            expected_sources=expected_sources,
        ),
    }
    build_provenance = _validate_package_build_provenance(
        receipt.get("build_provenance"),
        root=root,
        epoch=epoch,
    )
    installed_semantic_equivalence = _validate_installed_semantic_equivalence(
        receipt.get("installed_semantic_equivalence"),
        expected_sources=expected_sources,
        version=version,
    )
    return {
        "source_date_epoch": epoch,
        "artifacts": result,
        "archive_validation": archive_validation,
        "build_provenance": build_provenance,
        "installed_semantic_equivalence": installed_semantic_equivalence,
    }


def validate(
    *,
    root: Path,
    candidate_sha: str,
    selection_path: Path,
    performance_path: Path,
    commercial_path: Path,
    candidate_dir: Path,
    full_workflow_run_id: int,
    commercial_workflow_run_id: int,
) -> dict[str, Any]:
    if SHA40.fullmatch(candidate_sha) is None:
        raise PrerequisiteError("candidate SHA must be a lowercase 40-hex commit")
    if full_workflow_run_id <= 0 or commercial_workflow_run_id <= 0:
        raise PrerequisiteError("workflow run IDs must be positive integers")
    version = _project_version(root)
    selection, selection_input_sha256 = _load_object_with_digest(
        selection_path, label="commercial selection"
    )
    try:
        from tools.commercial_repo_smoke import load_selection

        strictly_validated_selection = load_selection(selection_path)
    except (ImportError, OSError, ValueError) as exc:
        raise PrerequisiteError(
            f"commercial selection failed strict protocol validation: {exc}"
        ) from exc
    if strictly_validated_selection != selection:
        raise PrerequisiteError("commercial selection changed during strict validation")
    repositories = _validate_selection(selection)
    performance, performance_input_sha256 = _load_object_with_digest(
        performance_path, label="performance receipt"
    )
    commercial, commercial_input_sha256 = _load_object_with_digest(
        commercial_path, label="commercial receipt"
    )
    performance_summary = _validate_performance(
        performance, candidate_sha=candidate_sha, root=root
    )
    commercial_summary = _validate_commercial(
        commercial,
        candidate_sha=candidate_sha,
        version=version,
        selection=selection,
        repositories=repositories,
    )
    package_summary = _validate_package(
        candidate_dir,
        root=root,
        candidate_sha=candidate_sha,
        version=version,
        expected_source_date_epoch=_candidate_commit_epoch(root, candidate_sha),
    )
    return {
        "schema": OUTPUT_SCHEMA,
        "candidate_sha": candidate_sha,
        "version": version,
        "workflow_runs": {
            "full_product": full_workflow_run_id,
            "commercial_100_repository": commercial_workflow_run_id,
        },
        "input_sha256": {
            "performance": performance_input_sha256,
            "commercial": commercial_input_sha256,
            "selection": selection_input_sha256,
        },
        "performance": performance_summary,
        "commercial": commercial_summary,
        "package": package_summary,
        "release_prerequisites_pass": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--performance-receipt", type=Path, required=True)
    parser.add_argument("--commercial-receipt", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--full-workflow-run-id", type=int, required=True)
    parser.add_argument("--commercial-workflow-run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate(
            root=args.root.resolve(),
            candidate_sha=args.candidate_sha,
            selection_path=args.selection,
            performance_path=args.performance_receipt,
            commercial_path=args.commercial_receipt,
            candidate_dir=args.candidate_dir,
            full_workflow_run_id=args.full_workflow_run_id,
            commercial_workflow_run_id=args.commercial_workflow_run_id,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt" and hasattr(os, "O_DIRECTORY"):
            parent_descriptor = os.open(
                args.output.parent, os.O_RDONLY | os.O_DIRECTORY
            )
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
    except (OSError, PrerequisiteError, KeyError, ValueError) as exc:
        sys.stderr.write(f"release prerequisite validation failed: {exc}\n")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
