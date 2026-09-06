"""Create and verify final SoftwareX archives from the checked public release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import zipfile

from research.softwarex.build_public_release import inspect, safe_name, SECRETS

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "research/softwarex"
SOURCE_FILES = ("main.tex", "references.bib", "main.bbl", "elsarticle.cls", "elsarticle-num.bst", "STYLE_SOURCE_NOTICE.txt")


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "duplicate archive member")
        require(archive.testzip() is None, "archive CRC failure")
        index = load_manifest_bytes(archive.read("SUBMISSION_ARCHIVE_MANIFEST.json"))
        require(set(names) == set(index) | {"SUBMISSION_ARCHIVE_MANIFEST.json"}, "archive inventory mismatch")
        for name, row in index.items():
            safe_name(name)
            raw = archive.read(name)
            require(len(raw) == row["bytes"] and digest(raw) == row["sha256"], "archive digest mismatch")
            require(not any(pattern.search(raw) for pattern in SECRETS), "possible secret in archive")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size, "members": len(names), "verified": True}


def load_manifest_bytes(raw):
    value = json.loads(raw)
    rows = value["files"]
    require(len(rows) == len({r["path"] for r in rows}), "duplicate manifest row")
    return {row["path"]: row for row in rows}


def write_archive(path, payloads, kind):
    require(not path.exists(), "refusing to overwrite an existing final archive")
    for name, raw in payloads.items():
        safe_name(name)
        require(not any(pattern.search(raw) for pattern in SECRETS), "possible secret in archive input")
    manifest = {"schema": "zerorun.softwarex-submission-archive.v1", "kind": kind,
        "author": "Jishan Kapoor", "files": [{"path": name, "bytes": len(raw), "sha256": digest(raw)} for name, raw in sorted(payloads.items())]}
    payloads = {**payloads, "SUBMISSION_ARCHIVE_MANIFEST.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (2026, 9, 6, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    return verify(path)


def build(release):
    checked = inspect(release)
    evidence = load(HERE / "generated/paper-evidence.json")
    require(evidence["preview"] is False and evidence["replication"]["completed"] is True, "final reconciled paper required")
    pdf_path = ROOT / "output/pdf/zerorun-softwarex.pdf"
    qa = load(HERE / "generated/pdf-review.json")
    require(qa["pdf_sha256"] == digest(pdf_path.read_bytes()) and qa["all_pages_visually_reviewed"] is True, "final PDF visual review missing")
    require(qa["unresolved_references"] is False and qa["word_limit_pass"] is True, "PDF references/length not checked")
    source = {name: (HERE / "paper" / name).read_bytes() for name in SOURCE_FILES}
    require(digest(source["main.tex"]) == evidence["main_tex_sha256"], "manuscript differs from reconciled build")
    require(digest(source["references.bib"]) == evidence["bibliography_sha256"], "bibliography differs from reconciled build")
    require(b"@@" not in source["main.tex"] and b"Layout preview" not in source["main.tex"], "unfinished source")
    cited = set()
    for match in re.finditer(rb"\\cite\{([^}]+)\}", source["main.tex"]):
        cited.update(match.group(1).decode().split(","))
    referenced = set(re.findall(rb"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", source["main.bbl"]))
    require({x.encode() for x in cited} == referenced, "compiled bibliography/citation mismatch")
    source_result = write_archive(ROOT / "output/submission/SoftwareX_source.zip", source, "flat-editable-manuscript")
    payloads = {row["path"]: (release / row["path"]).read_bytes() for row in checked["files"]}
    payloads["PUBLIC_RELEASE_MANIFEST.json"] = (release / "PUBLIC_RELEASE_MANIFEST.json").read_bytes()
    require(payloads.get("output/pdf/zerorun-softwarex.pdf") == pdf_path.read_bytes(), "public release lacks final PDF")
    require(payloads.get("research/softwarex/paper/main.tex") == source["main.tex"], "public release manuscript drift")
    reviewer_result = write_archive(ROOT / "output/submission/ZeroRun_SoftwareX_reviewer.zip", payloads, "reviewer-software-and-evidence")
    receipt = {"schema": "zerorun.softwarex-artifact-build.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_archive": source_result, "reviewer_archive": reviewer_result,
        "public_release_manifest_sha256": digest((release / "PUBLIC_RELEASE_MANIFEST.json").read_bytes()),
        "pdf_sha256": qa["pdf_sha256"], "private_history_included": False, "private_authority_included": False,
        "journal_submitted": False, "payment_made": False}
    target = HERE / "generated/artifact-build.json"
    require(not target.exists(), "artifact receipt already exists")
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=ROOT / "tmp/softwarex-public-release")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.verify) if args.verify else build(args.release), sort_keys=True))


if __name__ == "__main__":
    main()
