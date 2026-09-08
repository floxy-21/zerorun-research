"""Run the unchanged synthetic lifecycle with an explicit public src-layout adapter.

The source-package inventory root is adapted, and one hash-bound original
support module is supplied under a separately recorded pre-model amendment.
No eligibility, authority, response validation, prompt, or runtime is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys

PINNED_MANIFEST = "76bded3e8312517594c3977fa311c1cc4e3060bd7c7a390f34b5055f2cc632dc"
PROTOCOL_SHA256 = "da254c66e5fc60fd03a2ce3c5125a5850de5aedf69941e3ac982f2e45d3591a5"
AMENDMENT_SHA256 = "e5c844c1d03d9eb531af24517ae9e70c2ebbbf12ec4de6e8b6409bb8c93db7d6"
SUPPORT_HELPER = "aggregate_codex_install_evidence.py"
SUPPORT_HELPER_SHA256 = "a5eafbb44d36751bcf84dc353a6625a02b9dfb08e8a04a95e6b37a3bb689fc21"
SUPPORT_SOURCE_COMMIT = "f67cac4a9067bc7dc6debc86f31d5e62e9aedf78"
PROTECTED = {
    "tools/codex_agent_lifecycle.py": "9db6ac92823ddeb07505d4e982592c28f0e5252c444c890c53e8e26718923805",
    "tools/codex_agent_integration_smoke.py": "53a3c86f88aef0cee7a63571a5c22984b17d92831cb746b434f3e6ed5744f328",
    "tools/install_verified_codex_cli.py": "dfd2eab91d114c6d10847092da338d374b8d7833020a473d9b63edb8bc13fae3",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def real_directory(path):
    lexical = Path(os.path.abspath(Path(path).expanduser()))
    info = lexical.lstat()
    resolved = lexical.resolve(strict=True)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400 or lexical != resolved):
        raise ValueError("source path must be a real non-linked directory")
    return resolved


def preflight(root):
    root = real_directory(root)
    real_directory(root / "src")
    real_directory(root / "src/zerorun")
    if digest(root / "PUBLIC_RELEASE_MANIFEST.json") != PINNED_MANIFEST:
        raise ValueError("the pinned public experiment manifest differs")
    for relative, expected in PROTECTED.items():
        path = root / relative
        real_directory(path.parent)
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError("protected lifecycle helper differs: " + relative)
    manifest = json.loads((root / "PUBLIC_RELEASE_MANIFEST.json").read_bytes())
    rows = [row for row in manifest["files"] if row["path"].startswith("src/zerorun/")]
    if len(rows) != 36 or manifest["runtime_modified"] is not False:
        raise ValueError("unexpected public runtime inventory")
    actual = {path.relative_to(root).as_posix() for path in (root / "src/zerorun").glob("*.py")}
    if actual != {row["path"] for row in rows}:
        raise ValueError("public runtime file set differs")
    for row in rows:
        path = root / row["path"]
        if path.is_symlink() or digest(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("public runtime bytes differ: " + row["path"])
    return root


def inventory_adapter(original, approved_root):
    def adapted(source_root):
        if real_directory(source_root) != approved_root:
            raise ValueError("unexpected inventory source root")
        return original(approved_root / "src")
    return adapted


def checked_regular_file(path, expected, label):
    path = Path(os.path.abspath(Path(path).expanduser()))
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 0x400
            or path.resolve(strict=True) != path or before.st_size > 256 * 1024):
        raise ValueError(label + " must be a bounded regular non-linked file")
    actual = digest(path)
    after = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode,
                             item.st_size, item.st_mtime_ns,
                             getattr(item, "st_ctime_ns", None))
    if actual != expected or identity(before) != identity(after):
        raise ValueError(label + " bytes or identity differ")
    return path


def support_preflight(root, adapter_directory):
    adapter_directory = real_directory(adapter_directory)
    checked_regular_file(adapter_directory / "LIVE_CLIENT_AMENDMENT_1.md",
                         AMENDMENT_SHA256, "pre-model amendment")
    support = real_directory(adapter_directory / "support")
    support_tools = real_directory(support / "tools")
    if support_tools.is_relative_to(root):
        raise ValueError("support must remain outside the pinned source checkout")
    helper = checked_regular_file(support_tools / SUPPORT_HELPER,
                                  SUPPORT_HELPER_SHA256, "original support helper")
    return support_tools, helper


def validate_support_import(module, helper):
    if Path(module.__file__).resolve(strict=True) != helper:
        raise ValueError("wrong support helper module imported")
    checked_regular_file(Path(module.__file__), SUPPORT_HELPER_SHA256,
                         "imported original support helper")


def install_support_namespace(root, support_tools, helper, *, importer=None):
    importer = importer or importlib.import_module
    source_tools = real_directory(root / "tools")
    # The frozen public tools package is a namespace. Never execute a newly
    # inserted package initializer or let a source-local file shadow support.
    for unexpected in (source_tools / "__init__.py", source_tools / SUPPORT_HELPER,
                       support_tools / "__init__.py"):
        if unexpected.exists() or unexpected.is_symlink():
            raise ValueError("unexpected module shadows the frozen tools namespace")
    namespace = importer("tools")
    paths = getattr(namespace, "__path__", None)
    if getattr(namespace, "__file__", None) is not None or paths is None:
        raise ValueError("tools must be the checked public namespace package")
    resolved = [real_directory(Path(path)) for path in paths]
    if source_tools not in resolved or any(path not in {source_tools, support_tools} for path in resolved):
        raise ValueError("tools namespace contains an unexpected import path")
    if support_tools not in resolved:
        paths.append(str(support_tools))
    name = "tools.aggregate_codex_install_evidence"
    if name in sys.modules:
        validate_support_import(sys.modules[name], helper)
    module = importer(name)
    validate_support_import(module, helper)
    return module


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def seal_receipt(original, source_recheck):
    expected = canonical_hash({key: value for key, value in original.items() if key != "evidence_payload_sha256"})
    if original.get("evidence_payload_sha256") != expected:
        raise ValueError("original lifecycle receipt hash differs")
    envelope = {
        "schema": "zerorun.softwarex-public-lifecycle.v1",
        "original_receipt": original,
        "source_recheck": source_recheck,
        "functional_lifecycle_pass": original["functional_lifecycle_pass"] is True and source_recheck["passed"] is True,
        "evidence_valid": original.get("evidence_valid") is True and source_recheck["passed"] is True,
        "research_public_layout_adapter": {
            "schema": "zerorun.public-lifecycle-layout-adapter.v1",
            "adapter_sha256": digest(Path(__file__)),
            "protocol_sha256": PROTOCOL_SHA256,
            "pinned_public_manifest_sha256": PINNED_MANIFEST,
            "protected_helpers_sha256": PROTECTED,
            "only_adaptation": "source-package inventory uses root/src/zerorun instead of root/zerorun",
            "pre_model_support_amendment": {
                "schema": "zerorun.public-lifecycle-support-amendment.v1",
                "amendment_file": "LIVE_CLIENT_AMENDMENT_1.md",
                "amendment_sha256": AMENDMENT_SHA256,
                "helper_path": "support/tools/" + SUPPORT_HELPER,
                "helper_sha256": SUPPORT_HELPER_SHA256,
                "helper_original_path": "tools/" + SUPPORT_HELPER,
                "helper_source_commit": SUPPORT_SOURCE_COMMIT,
                "source_checkout_modified": False,
            },
            "runtime_modified": False,
            "original_validation_and_authority_unchanged": True,
        },
    }
    envelope["evidence_payload_sha256"] = canonical_hash(envelope)
    return envelope


def validate_imports(root, modules):
    for relative, module in modules.items():
        if Path(module.__file__).resolve(strict=True) != root / relative:
            raise ValueError("wrong protected module imported: " + relative)


def main(argv=None):
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--source-root", required=True, type=Path)
    supplied, _ = early.parse_known_args(argv)
    root = preflight(supplied.source_root)
    if digest(Path(__file__).with_name("LIVE_CLIENT_PROTOCOL.md")) != PROTOCOL_SHA256:
        raise ValueError("actual live-client protocol differs")
    support_tools, helper = support_preflight(root, Path(__file__).parent)
    sys.path.insert(0, str(root))
    support_module = install_support_namespace(root, support_tools, helper)
    lifecycle = importlib.import_module("tools.codex_agent_lifecycle")
    smoke = importlib.import_module("tools.codex_agent_integration_smoke")
    installer = importlib.import_module("tools.install_verified_codex_cli")
    validate_imports(root, {"tools/codex_agent_lifecycle.py": lifecycle,
                           "tools/codex_agent_integration_smoke.py": smoke,
                           "tools/install_verified_codex_cli.py": installer})
    if lifecycle.codex_install_evidence is not support_module:
        raise ValueError("lifecycle did not import the checked support helper")
    args = lifecycle._parser().parse_args(argv)
    smoke._validate_output_destination(args.output)
    original = lifecycle._source_package_identity
    lifecycle._source_package_identity = inventory_adapter(original, root)
    try:
        receipt = lifecycle.run_synthetic_lifecycle(
            root, runtime_image=args.runtime_image,
            approve_remote_metadata=args.approve_remote_metadata,
            approve_synthetic_formative_authority=args.approve_synthetic_formative_authority,
            codex_command=args.codex_command,
            codex_install_receipt=args.codex_install_receipt,
            codex_main_integrity=args.codex_main_integrity,
            codex_platform_integrity=args.codex_platform_integrity,
            expected_main_commit=args.expected_main_commit,
            zerorun_python_command=args.zerorun_python_command,
            git_command=args.git_command, timeout_seconds=args.timeout_seconds,
            output_limit_bytes=args.output_limit_bytes, work_root=args.work_root,
        )
    finally:
        lifecycle._source_package_identity = original
    try:
        preflight(root)
        support_preflight(root, Path(__file__).parent)
        validate_support_import(support_module, helper)
        validate_imports(root, {"tools/codex_agent_lifecycle.py": lifecycle,
                               "tools/codex_agent_integration_smoke.py": smoke,
                               "tools/install_verified_codex_cli.py": installer})
        source_recheck = {"passed": True}
    except (OSError, ValueError) as exc:
        source_recheck = {"passed": False, "error_type": type(exc).__name__, "error": str(exc)}
    envelope = seal_receipt(receipt, source_recheck)
    smoke.write_receipt_atomic(args.output, envelope)
    print(json.dumps({"receipt": str(args.output), "functional_lifecycle_pass": envelope["functional_lifecycle_pass"],
                      "status": receipt["status"], "agent_stages": len(receipt["agent_stages"])}))
    return 0 if envelope["functional_lifecycle_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
