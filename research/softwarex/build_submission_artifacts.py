"""Create and verify final SoftwareX archives from the checked public release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid
import zipfile

from research.softwarex.build_public_release import inspect, safe_name, SECRETS

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "research/softwarex"
SOURCE_FILES = ("main.tex", "references.bib", "main.bbl", "elsarticle.cls", "elsarticle-num.bst", "STYLE_SOURCE_NOTICE.txt")
ARCHIVE_MANIFEST = "SUBMISSION_ARCHIVE_MANIFEST.json"
MAX_MEMBERS = 20_000
MAX_MEMBER_BYTES = 80 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
KINDS = {"flat-editable-manuscript", "reviewer-software-and-evidence"}
PRIOR_ARTIFACTS = {"output/submission/SoftwareX_source.zip", "output/submission/ZeroRun_SoftwareX_reviewer.zip",
                   "research/softwarex/generated/artifact-build.json"}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return strict_json(read_regular(path))


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("nonfinite JSON value")

    def finite(value):
        result = float(value)
        require(math.isfinite(result), "nonfinite JSON value")
        return result

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject, parse_float=finite)


def display(path):
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def read_regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and not getattr(info, "st_file_attributes", 0) & 0x400, "nonregular source file")
    require(info.st_size <= MAX_MEMBER_BYTES, "oversized source file")
    raw = path.read_bytes()
    after = path.lstat()
    require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "source file changed while reading")
    return raw


def member_name(name):
    require(isinstance(name, str) and name, "invalid archive member name")
    relative = safe_name(name)
    require(relative.as_posix() == name, "noncanonical archive member name")
    return relative


def scan_secret(raw):
    require(not any(pattern.search(raw) for pattern in SECRETS), "possible secret in archive")


def verify(path):
    path = Path(path).absolute()
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink()
            and not getattr(before, "st_file_attributes", 0) & 0x400, "nonregular archive")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        require(0 < len(infos) <= MAX_MEMBERS, "archive member count bound exceeded")
        require(len(names) == len(set(names)), "duplicate archive member")
        require(sum(info.file_size for info in infos) <= MAX_TOTAL_BYTES, "archive expansion bound exceeded")
        for info in infos:
            member_name(info.filename)
            require(0 <= info.file_size <= MAX_MEMBER_BYTES, "archive member byte bound exceeded")
            require(not info.is_dir() and stat.S_ISREG(info.external_attr >> 16), "archive link/directory/special member")
            require(not info.flag_bits & 1 and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                    "unsupported encrypted/compressed member")
        require(ARCHIVE_MANIFEST in names, "missing archive manifest")
        require(archive.getinfo(ARCHIVE_MANIFEST).file_size <= MAX_MANIFEST_BYTES, "oversized archive manifest")
        manifest_raw = archive.read(ARCHIVE_MANIFEST)
        scan_secret(manifest_raw)
        index = load_manifest_bytes(manifest_raw)
        require(set(names) == set(index) | {ARCHIVE_MANIFEST}, "archive inventory mismatch")
        if strict_json(manifest_raw)["kind"] == "flat-editable-manuscript":
            require(set(index) == set(SOURCE_FILES), "source archive is not the complete flat bundle")
        for name, row in index.items():
            require(archive.getinfo(name).file_size == row["bytes"], "archive declared size mismatch")
            raw = archive.read(name)
            require(len(raw) == row["bytes"] and digest(raw) == row["sha256"], "archive digest mismatch")
            scan_secret(raw)
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "archive changed during verification")
    return {"path": display(path), "sha256": digest(path.read_bytes()), "bytes": after.st_size, "members": len(names), "verified": True}


def load_manifest_bytes(raw):
    require(len(raw) <= MAX_MANIFEST_BYTES, "oversized archive manifest")
    value = strict_json(raw)
    require(isinstance(value, dict) and set(value) == {"schema", "kind", "author", "files"},
            "invalid archive manifest fields")
    require(value["schema"] == "zerorun.softwarex-submission-archive.v1"
            and isinstance(value["kind"], str) and value["kind"] in KINDS
            and value["author"] == "Jishan Kapoor", "invalid archive manifest identity")
    rows = value["files"]
    require(isinstance(rows, list) and 0 < len(rows) < MAX_MEMBERS, "invalid manifest file count")
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}, "invalid manifest row")
        member_name(row["path"])
        require(row["path"] != ARCHIVE_MANIFEST, "manifest cannot index itself")
        require(type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_MEMBER_BYTES, "invalid manifest byte count")
        require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid manifest hash")
    require(sum(row["bytes"] for row in rows) <= MAX_TOTAL_BYTES, "manifest total byte bound exceeded")
    require(len(rows) == len({r["path"] for r in rows}), "duplicate manifest row")
    return {row["path"]: row for row in rows}


def validated_payloads(payloads, kind):
    require(isinstance(kind, str) and kind in KINDS and isinstance(payloads, dict)
            and 0 < len(payloads) < MAX_MEMBERS, "invalid archive request")
    require(ARCHIVE_MANIFEST not in payloads, "reserved archive manifest name")
    for name, raw in payloads.items():
        member_name(name)
        require(isinstance(raw, bytes) and len(raw) <= MAX_MEMBER_BYTES, "invalid archive input bytes")
        scan_secret(raw)
    require(sum(len(raw) for raw in payloads.values()) <= MAX_TOTAL_BYTES, "archive input total bound exceeded")
    if kind == "flat-editable-manuscript":
        require(set(payloads) == set(SOURCE_FILES), "source bundle must contain exactly the six flat compilation inputs")
    manifest = {"schema": "zerorun.softwarex-submission-archive.v1", "kind": kind,
        "author": "Jishan Kapoor", "files": [{"path": name, "bytes": len(raw), "sha256": digest(raw)} for name, raw in sorted(payloads.items())]}
    return {**payloads, ARCHIVE_MANIFEST: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()}


def output_parent(path):
    path = Path(path).absolute()
    require(path.is_relative_to(ROOT) and ".." not in path.parts, "output outside artifact workspace")
    cursor = ROOT
    for part in path.parent.relative_to(ROOT).parts:
        cursor = cursor / part
        if not cursor.exists():
            cursor.mkdir()
        info = cursor.lstat()
        require(stat.S_ISDIR(info.st_mode) and not cursor.is_symlink()
                and not getattr(info, "st_file_attributes", 0) & 0x400, "linked output directory")
    require(not path.exists() and not path.is_symlink(), "refusing to overwrite an existing final artifact")
    return path


def new_stage(path):
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".building", dir=path.parent)
    staged = Path(name)
    info = staged.lstat()
    return descriptor, {"path": staged, "parent": path.parent.resolve(), "device": info.st_dev, "inode": info.st_ino}


def require_owned_stage(stage):
    path = stage["path"]
    require(path.parent.resolve() == stage["parent"] and path.name.endswith(".building"),
            "staging cleanup path escaped its recorded parent")
    require(path.is_relative_to(ROOT), "staging cleanup path escaped workspace")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and (info.st_dev, info.st_ino) == (stage["device"], stage["inode"]),
            "staging file identity changed")
    return path


def cleanup_stage(stage):
    require_owned_stage(stage).unlink()


def stage_archive(path, payloads, kind, stages):
    payloads = validated_payloads(payloads, kind)
    path = output_parent(path)
    descriptor, stage = new_stage(path)
    stages.append(stage)
    with os.fdopen(descriptor, "w+b") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, raw in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, (2026, 9, 6, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, raw)
        stream.flush()
        os.fsync(stream.fileno())
    result = verify(stage["path"])
    stage["sha256"] = result["sha256"]
    return stage, {**result, "path": display(path)}


def publish_stage(stage, final):
    source = require_owned_stage(stage)
    require(digest(source.read_bytes()) == stage["sha256"], "staged bytes changed before publication")
    # link() is an exclusive publication on both POSIX and Windows: unlike
    # rename()/replace(), it cannot overwrite a destination that appeared.
    os.link(source, final)
    require(final.stat().st_ino == stage["inode"], "published file does not match staged inode")


def write_archive(path, payloads, kind):
    stages = []
    try:
        stage, result = stage_archive(path, payloads, kind, stages)
        publish_stage(stage, Path(path))
        return result
    finally:
        for stage in stages:
            cleanup_stage(stage)


def validate_flat_source(source):
    require(all(source[name].strip() for name in SOURCE_FILES), "empty compilation source")
    document = re.sub(rb"(?<!\\)%[^\n]*", b"", source["main.tex"])
    require(re.search(rb"\\documentclass(?:\[[^\]]*\])?\{elsarticle\}", document), "wrong manuscript document class")
    for pattern, expected in ((rb"\\bibliography\{([^}]+)\}", b"references"),
                              (rb"\\bibliographystyle\{([^}]+)\}", b"elsarticle-num")):
        require(re.findall(pattern, document) == [expected], "unbundled bibliography dependency")
    for dependency in re.findall(rb"\\(?:input|include|includegraphics|includepdf|lstinputlisting|VerbatimInput)(?:\[[^\]]*\])?\{([^}]+)\}", document):
        require(dependency.decode() in SOURCE_FILES and dependency != b"main.tex", "unbundled external manuscript input")
    require(b"@@" not in document and b"Layout preview" not in document, "unfinished source")
    cited = set()
    for match in re.finditer(rb"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\]){0,2}\{([^}]+)\}", document):
        cited.update(key.strip() for key in match.group(1).split(b","))
    referenced = re.findall(rb"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", source["main.bbl"])
    require(len(referenced) == len(set(referenced)) and cited == set(referenced), "compiled bibliography/citation mismatch")


def build(release):
    source_path = ROOT / "output/submission/SoftwareX_source.zip"
    reviewer_path = ROOT / "output/submission/ZeroRun_SoftwareX_reviewer.zip"
    target = HERE / "generated/artifact-build.json"
    # Refuse collisions before creating even a temporary archive.
    for path in (source_path, reviewer_path, target):
        require(not path.exists() and not path.is_symlink(), "final artifact/receipt already exists")
    checked = inspect(release)
    require(not PRIOR_ARTIFACTS.intersection(row["path"] for row in checked["files"]),
            "input release already contains submission artifacts or an old completion receipt")
    require(not checked.get("external_artifacts"), "pre-archive input must not declare an earlier external reviewer artifact")
    evidence_raw = read_regular(HERE / "generated/paper-evidence.json")
    evidence = strict_json(evidence_raw)
    require(evidence["preview"] is False and evidence["replication"]["completed"] is True, "final reconciled paper required")
    pdf_path = ROOT / "output/pdf/zerorun-softwarex.pdf"
    pdf_raw = read_regular(pdf_path)
    require(pdf_raw.startswith(b"%PDF-"), "final PDF signature missing")
    qa_raw = read_regular(HERE / "generated/pdf-review.json")
    qa = strict_json(qa_raw)
    require(qa["pdf_sha256"] == digest(pdf_raw) and qa["all_pages_visually_reviewed"] is True, "final PDF visual review missing")
    require(qa["unresolved_references"] is False and qa["word_limit_pass"] is True, "PDF references/length not checked")
    source = {name: read_regular(HERE / "paper" / name) for name in SOURCE_FILES}
    compilation_hashes = {name: digest(raw) for name, raw in source.items()}
    require(qa.get("compilation_source_sha256") == compilation_hashes, "reviewed PDF compilation-source binding mismatch")
    require(digest(source["main.tex"]) == evidence["main_tex_sha256"], "manuscript differs from reconciled build")
    require(digest(source["references.bib"]) == evidence["bibliography_sha256"], "bibliography differs from reconciled build")
    validate_flat_source(source)
    payloads = {}
    for row in checked["files"]:
        raw = read_regular(release / row["path"])
        require(digest(raw) == row["sha256"] and len(raw) == row["bytes"], "release changed after inventory verification")
        payloads[row["path"]] = raw
    release_manifest_raw = read_regular(release / "PUBLIC_RELEASE_MANIFEST.json")
    require(strict_json(release_manifest_raw) == checked, "public manifest changed after verification")
    payloads["PUBLIC_RELEASE_MANIFEST.json"] = release_manifest_raw
    require(payloads.get("output/pdf/zerorun-softwarex.pdf") == pdf_raw, "public release lacks final PDF")
    require(all(payloads.get("research/softwarex/paper/" + name) == raw for name, raw in source.items()),
            "public release compilation-source drift")
    # Both payloads are fully preflighted before any artifact is staged.
    validated_payloads(source, "flat-editable-manuscript")
    validated_payloads(payloads, "reviewer-software-and-evidence")
    stages, published = [], []
    try:
        source_stage, source_result = stage_archive(source_path, source, "flat-editable-manuscript", stages)
        reviewer_stage, reviewer_result = stage_archive(reviewer_path, payloads, "reviewer-software-and-evidence", stages)
        receipt = {"schema": "zerorun.softwarex-artifact-build.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_archive": source_result, "reviewer_archive": reviewer_result,
        "public_release_manifest_sha256": digest(release_manifest_raw),
        "paper_evidence_sha256": digest(evidence_raw), "pdf_review_sha256": digest(qa_raw),
        "compilation_source_sha256": compilation_hashes, "builder_sha256": digest(Path(__file__).read_bytes()),
        "pdf_sha256": qa["pdf_sha256"], "private_history_included": False, "private_authority_included": False,
        "journal_submitted": False, "payment_made": False, "completed": True}
        output_parent(target)
        descriptor, receipt_stage = new_stage(target)
        stages.append(receipt_stage)
        receipt_raw = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(receipt_raw)
            stream.flush()
            os.fsync(stream.fileno())
        receipt_stage["sha256"] = digest(receipt_raw)
        for stage, final in ((source_stage, source_path), (reviewer_stage, reviewer_path), (receipt_stage, target)):
            publish_stage(stage, final)
            published.append(display(final))
        return receipt
    except BaseException as error:
        # Never delete published finals or somebody else's files. A rare
        # publication race can leave verified partial finals; record them
        # explicitly rather than pretending publication was transactional.
        failure = HERE / "generated" / ("artifact-build-failure-" + uuid.uuid4().hex + ".json")
        output_parent(failure)
        with failure.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"schema": "zerorun.softwarex-artifact-build-failure.v1", "completed": False,
                       "error_type": type(error).__name__, "error": str(error),
                       "published_outputs_retained": published, "retry_overwrites_permitted": False}, stream, indent=2)
            stream.write("\n")
        raise
    finally:
        for stage in stages:
            cleanup_stage(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=ROOT / "tmp/softwarex-public-release")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.verify) if args.verify else build(args.release), sort_keys=True))


if __name__ == "__main__":
    main()
