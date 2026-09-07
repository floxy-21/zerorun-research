#!/usr/bin/env python3
"""Validate and aggregate exact Codex installer receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import install_verified_codex_cli as installer
from zerorun.bounded_json import JsonLimits, loads_bounded_json
from zerorun.model import ConfigurationError


SCHEMA = "zerorun.verified-codex-install-aggregate.v2"
MAX_RECEIPT_BYTES = 512 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RECEIPT_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_RECEIPT_BYTES,
    max_depth=32,
    max_values=20_000,
    max_object_members=2_000,
    max_structural_tokens=80_000,
    max_number_chars=256,
    max_string_chars=128 * 1024,
    max_total_string_chars=MAX_RECEIPT_BYTES,
    allow_floats=False,
)


class EvidenceError(RuntimeError):
    """An installer receipt is incomplete, inconsistent, or untrusted."""


def _regular_bytes(path: Path) -> bytes:
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
            raise EvidenceError(
                f"installer receipt is not a regular non-linked file: {path}"
            )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_file_attributes", 0) & _REPARSE_POINT
                or identity != (before.st_dev, before.st_ino)
                or opened.st_size < 2
                or opened.st_size > MAX_RECEIPT_BYTES
            ):
                raise EvidenceError(
                    f"installer receipt size or identity is invalid: {path}"
                )
            chunks: list[bytes] = []
            remaining = MAX_RECEIPT_BYTES + 1
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
                or len(raw) > MAX_RECEIPT_BYTES
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
                raise EvidenceError(f"installer receipt changed while being read: {path}")
        finally:
            os.close(descriptor)
        return raw
    except EvidenceError:
        raise
    except OSError as exc:
        raise EvidenceError(f"installer receipt is unavailable: {path}: {exc}") from exc


def _load_receipt(path: Path) -> tuple[bytes, object]:
    raw = _regular_bytes(path)
    try:
        value = loads_bounded_json(
            raw,
            label=f"installer receipt {path}",
            limits=_RECEIPT_JSON_LIMITS,
        )
    except ConfigurationError as exc:
        raise EvidenceError(
            f"installer receipt is not bounded strict JSON: {path}: {exc}"
        ) from exc
    return raw, value


def _digest(value: object) -> str:
    return installer._canonical_sha256(value)


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise EvidenceError(f"{label} is not a canonical SHA-256 digest")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise EvidenceError(f"{label} is outside its integer bound")
    return value


def _exact_keys(value: object, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceError(f"{label} has an unexpected structure")
    return value


def _validate_runtime(value: object, *, name: str) -> dict[str, Any]:
    row = _exact_keys(
        value,
        {
            "command",
            "lexical_path",
            "lexical_kind",
            "link_target",
            "resolved_path",
            "resolved_bytes",
            "resolved_sha256",
            "reported_version",
        },
        label=f"{name} runtime identity",
    )
    if (
        row["command"] != name
        or row["lexical_kind"] not in {"regular_file", "symlink"}
        or not isinstance(row["lexical_path"], str)
        or not os.path.isabs(row["lexical_path"])
        or not isinstance(row["resolved_path"], str)
        or not os.path.isabs(row["resolved_path"])
        or not isinstance(row["reported_version"], str)
        or not row["reported_version"]
        or len(row["reported_version"]) > 256
        or any(character in row["reported_version"] for character in "\r\n")
        or (row["lexical_kind"] == "regular_file" and row["link_target"] is not None)
        or (row["lexical_kind"] == "symlink" and not isinstance(row["link_target"], str))
    ):
        raise EvidenceError(f"{name} runtime identity is malformed")
    _positive_int(row["resolved_bytes"], label=f"{name} runtime bytes", maximum=installer.MAX_RUNTIME_BYTES)
    _sha(row["resolved_sha256"], label=f"{name} runtime digest")
    return row


def _validate_package(
    value: object,
    *,
    label: str,
    expected_url: str,
    expected_integrity: str,
    maximum_bytes: int,
    maximum_files: int,
    maximum_unpacked_bytes: int,
) -> dict[str, Any]:
    row = _exact_keys(
        value,
        {
            "url",
            "integrity",
            "sha256",
            "bytes",
            "metadata_sha256",
            "regular_files",
            "unpacked_bytes",
            "member_manifest_sha256",
        },
        label=label,
    )
    if row["url"] != expected_url or row["integrity"] != expected_integrity:
        raise EvidenceError(f"{label} URL or SRI is not the frozen value")
    try:
        installer._parse_sri(row["integrity"])
    except installer.InstallError as exc:
        raise EvidenceError(f"{label} SRI is malformed") from exc
    _sha(row["sha256"], label=f"{label} archive digest")
    _sha(row["metadata_sha256"], label=f"{label} metadata digest")
    _sha(row["member_manifest_sha256"], label=f"{label} member manifest digest")
    _positive_int(row["bytes"], label=f"{label} archive bytes", maximum=maximum_bytes)
    _positive_int(row["regular_files"], label=f"{label} file count", maximum=maximum_files)
    _positive_int(
        row["unpacked_bytes"],
        label=f"{label} unpacked bytes",
        maximum=maximum_unpacked_bytes,
    )
    return row


def _validate_loaded_receipt(
    path: Path,
    *,
    raw: bytes,
    value: object,
    expected_id: str,
    expected_source_commit: str,
    version: str,
    main_integrity: str,
    platform_integrity: str,
) -> dict[str, Any]:
    if installer.EVIDENCE_ID_RE.fullmatch(expected_id) is None:
        raise EvidenceError("expected installer evidence ID is invalid")
    if (
        installer.COMMIT_RE.fullmatch(expected_source_commit) is None
        or expected_source_commit == "0" * 40
    ):
        raise EvidenceError("expected source commit is invalid")
    if installer.VERSION_RE.fullmatch(version) is None:
        raise EvidenceError("expected Codex version is invalid")
    row = _exact_keys(
        value,
        {
            "schema",
            "evidence_id",
            "source_commit",
            "codex_version",
            "platform",
            "main_package",
            "platform_package",
            "installed",
            "runtime",
            "installer_source_sha256",
            "network",
            "node_version",
            "network_after_integrity_verification",
            "installation_method",
            "evidence_payload_sha256",
        },
        label="installer receipt",
    )
    canonical = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if raw != canonical:
        raise EvidenceError(f"installer receipt does not use canonical serialization: {path}")
    payload = {key: value for key, value in row.items() if key != "evidence_payload_sha256"}
    if row["evidence_payload_sha256"] != _digest(payload):
        raise EvidenceError(f"installer receipt payload digest mismatch: {path}")
    if (
        row["schema"] != installer.SCHEMA
        or row["evidence_id"] != expected_id
        or row["source_commit"] != expected_source_commit
        or row["codex_version"] != version
        or row["platform"] != "linux-x64"
        or row["installation_method"] != "direct-authenticated-archive-extraction"
        or row["network_after_integrity_verification"] is not False
        or row["installer_source_sha256"] != installer._sha256(Path(installer.__file__).resolve())
    ):
        raise EvidenceError(f"installer receipt is not bound to source/version/method: {path}")
    main_url = f"{installer.REGISTRY_ORIGIN}/@openai/codex/-/codex-{version}.tgz"
    platform_url = f"{installer.REGISTRY_ORIGIN}/@openai/codex/-/codex-{version}-linux-x64.tgz"
    main = _validate_package(
        row["main_package"],
        label="main package",
        expected_url=main_url,
        expected_integrity=main_integrity,
        maximum_bytes=installer.MAX_MAIN_BYTES,
        maximum_files=installer.MAX_MAIN_FILES,
        maximum_unpacked_bytes=installer.MAX_MAIN_UNPACKED_BYTES,
    )
    platform_row = _validate_package(
        row["platform_package"],
        label="platform package",
        expected_url=platform_url,
        expected_integrity=platform_integrity,
        maximum_bytes=installer.MAX_PLATFORM_BYTES,
        maximum_files=installer.MAX_PLATFORM_FILES,
        maximum_unpacked_bytes=installer.MAX_PLATFORM_UNPACKED_BYTES,
    )
    installed = _exact_keys(
        row["installed"],
        {
            "package_tree_sha256",
            "package_tree_regular_files",
            "package_tree_bytes",
            "launcher_sha256",
            "native_sha256",
            "vendor_regular_files",
            "vendor_unpacked_bytes",
            "command_kind",
            "command_target",
            "command_resolves_to_launcher",
        },
        label="installed tree identity",
    )
    for key in ("package_tree_sha256", "launcher_sha256", "native_sha256"):
        _sha(installed[key], label=f"installed {key}")
    package_files = _positive_int(
        installed["package_tree_regular_files"],
        label="installed tree file count",
        maximum=installer.MAX_MAIN_FILES + installer.MAX_PLATFORM_FILES,
    )
    _positive_int(
        installed["package_tree_bytes"],
        label="installed tree bytes",
        maximum=installer.MAX_MAIN_UNPACKED_BYTES + installer.MAX_PLATFORM_UNPACKED_BYTES,
    )
    vendor_files = _positive_int(
        installed["vendor_regular_files"],
        label="installed vendor file count",
        maximum=installer.MAX_PLATFORM_FILES,
    )
    _positive_int(
        installed["vendor_unpacked_bytes"],
        label="installed vendor bytes",
        maximum=installer.MAX_PLATFORM_UNPACKED_BYTES,
    )
    if (
        installed["command_kind"] != "relative_symlink"
        or installed["command_target"] != "../lib/node_modules/@openai/codex/bin/codex.js"
        or installed["command_resolves_to_launcher"] is not True
        or package_files != main["regular_files"] + vendor_files
        or vendor_files > platform_row["regular_files"]
    ):
        raise EvidenceError("installed tree accounting or launcher binding is invalid")
    runtime = _exact_keys(
        row["runtime"],
        {
            "node",
            "runtime_identity_unchanged_after_retrieval",
        },
        label="runtime identity",
    )
    node = _validate_runtime(runtime["node"], name="node")
    if (
        runtime["runtime_identity_unchanged_after_retrieval"] is not True
        or row["node_version"] != node["reported_version"]
    ):
        raise EvidenceError("Node runtime provenance is invalid")
    network = _exact_keys(
        row["network"],
        {
            "fixed_https_archive_downloads",
            "integrity_verified_before_prefix_creation",
            "subprocesses_after_integrity_verification",
            "network_operations_after_integrity_verification",
            "package_manager_invoked",
        },
        label="network boundary",
    )
    if network != {
        "fixed_https_archive_downloads": 2,
        "integrity_verified_before_prefix_creation": True,
        "subprocesses_after_integrity_verification": 0,
        "network_operations_after_integrity_verification": 0,
        "package_manager_invoked": False,
    }:
        raise EvidenceError("post-verification offline boundary is invalid")
    return {
        "evidence_id": expected_id,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "payload_sha256": row["evidence_payload_sha256"],
        "main_archive_sha256": main["sha256"],
        "platform_archive_sha256": platform_row["sha256"],
        "installed_tree_sha256": installed["package_tree_sha256"],
        "launcher_sha256": installed["launcher_sha256"],
        "native_sha256": installed["native_sha256"],
        "node": node,
    }


def validate_receipt(
    path: Path,
    *,
    expected_id: str,
    expected_source_commit: str,
    version: str,
    main_integrity: str,
    platform_integrity: str,
) -> dict[str, Any]:
    raw, value = _load_receipt(path)
    return _validate_loaded_receipt(
        path,
        raw=raw,
        value=value,
        expected_id=expected_id,
        expected_source_commit=expected_source_commit,
        version=version,
        main_integrity=main_integrity,
        platform_integrity=platform_integrity,
    )


def aggregate(
    receipts: Sequence[Path],
    *,
    expected_ids: Sequence[str],
    expected_source_commit: str,
    version: str,
    main_integrity: str,
    platform_integrity: str,
) -> dict[str, Any]:
    if not receipts or len(receipts) != len(expected_ids):
        raise EvidenceError("receipt and expected-ID counts must be equal and nonzero")
    if len(set(expected_ids)) != len(expected_ids):
        raise EvidenceError("expected installer evidence IDs are duplicated")
    if any(installer.EVIDENCE_ID_RE.fullmatch(value) is None for value in expected_ids):
        raise EvidenceError("an expected installer evidence ID is invalid")
    rows_by_id: dict[str, dict[str, Any]] = {}
    for path in receipts:
        raw, candidate = _load_receipt(path)
        evidence_id = candidate.get("evidence_id") if isinstance(candidate, dict) else None
        if not isinstance(evidence_id, str) or evidence_id not in expected_ids:
            raise EvidenceError(f"unexpected installer evidence ID: {path}")
        if evidence_id in rows_by_id:
            raise EvidenceError(f"duplicate installer evidence ID: {evidence_id}")
        rows_by_id[evidence_id] = _validate_loaded_receipt(
            path,
            raw=raw,
            value=candidate,
            expected_id=evidence_id,
            expected_source_commit=expected_source_commit,
            version=version,
            main_integrity=main_integrity,
            platform_integrity=platform_integrity,
        )
    if set(rows_by_id) != set(expected_ids):
        raise EvidenceError("installer receipt denominator is incomplete")
    rows = [rows_by_id[value] for value in expected_ids]
    immutable_keys = (
        "main_archive_sha256",
        "platform_archive_sha256",
        "installed_tree_sha256",
        "launcher_sha256",
        "native_sha256",
    )
    for key in immutable_keys:
        if len({row[key] for row in rows}) != 1:
            raise EvidenceError(f"authenticated Codex installation identities differ: {key}")
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source_commit": expected_source_commit,
        "codex_version": version,
        "receipt_count": len(rows),
        "expected_ids": list(expected_ids),
        "complete_denominator": True,
        "authenticated_installation_identical_across_receipts": True,
        "immutable_installation": {key: rows[0][key] for key in immutable_keys},
        "receipts": rows,
    }
    return {**payload, "evidence_payload_sha256": _digest(payload)}


def _write_output(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise EvidenceError("aggregate output must not already exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", action="append", required=True, type=Path)
    parser.add_argument("--expected-id", action="append", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--main-integrity", required=True)
    parser.add_argument("--linux-x64-integrity", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = aggregate(
            args.receipt,
            expected_ids=args.expected_id,
            expected_source_commit=args.expected_source_commit,
            version=args.version,
            main_integrity=args.main_integrity,
            platform_integrity=args.linux_x64_integrity,
        )
        _write_output(args.output, result)
    except (EvidenceError, OSError, ValueError, TypeError) as exc:
        print(f"Codex install evidence refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
