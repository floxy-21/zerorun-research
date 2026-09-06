"""Verify an allowlisted VM export and add only missing immutable evidence."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from research.sqj.export_public_evidence import EXCLUDED, ALLOWED_SUFFIXES
from research.sqj.analyze_campaign import read_json


def import_archive(archive_path, receipt_path, destination):
    receipt = read_json(receipt_path)
    destination = destination.absolute()
    if destination.is_symlink():
        raise ValueError("destination cannot be a symlink")
    resolved_destination = destination.resolve()
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != receipt["archive_sha256"]:
        raise ValueError("archive digest mismatch")
    expected = {row["path"]: row for row in receipt["files"]}
    if len(expected) != len(receipt["files"]):
        raise ValueError("duplicate evidence manifest path")
    payloads = []
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != set(expected):
            raise ValueError("archive inventory differs from receipt")
        for member in members:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts or "\\" in member.name or ":" in member.name or set(path.parts) & EXCLUDED or path.suffix not in ALLOWED_SUFFIXES:
                raise ValueError("unsafe/non-evidence archive member")
            if member.size > 80 * 1024 * 1024:
                raise ValueError("oversized evidence member")
            raw = archive.extractfile(member).read()
            row = expected[member.name]
            if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("evidence file digest mismatch")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(resolved_destination):
                raise ValueError("evidence target escapes destination")
            if any(parent.is_symlink() for parent in (target, *target.parents) if parent != destination.parent):
                raise ValueError("evidence target has a symlink component")
            if target.exists() and target.read_bytes() != raw:
                raise ValueError("refusing to overwrite existing different evidence: " + str(path))
            payloads.append((target, raw))
    for target, raw in payloads:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(raw)
    return len(payloads)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps({"verified_files": import_archive(args.archive, args.receipt, args.destination)}))
