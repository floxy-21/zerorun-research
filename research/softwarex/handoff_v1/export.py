"""Copy record-only evidence; never export private authentication keys."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import run as h
from .validate import validate_saved

BLOCKED = {"workspace", "source", "preflight-workspace", "dependency-starter", ".git", ".zerorun", ".zerorun-env",
           "private-cache-authentication-NOT-FOR-PUBLICATION", "__pycache__"}


def export(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve(strict=False)
    h.require(not destination.exists() and not destination.is_relative_to(source), "new external export destination required")
    summary = validate_saved(source)
    candidates = []
    for path in sorted(source.rglob("*"), key=lambda p: p.relative_to(source).as_posix()):
        relative = path.relative_to(source)
        if set(relative.parts) & BLOCKED or not path.is_file():
            continue
        h.require(path.suffix in {".json", ".py", ".md", ".log"}, "unexpected study output type")
        raw = h.ordinary(path)
        candidates.append((relative, raw))
    destination.mkdir(parents=True)
    rows = []
    for relative, raw in candidates:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        rows.append({"path": relative.as_posix(), "bytes": len(raw), "sha256": h.sha(raw)})
    h.save(destination / "EXPORT_MANIFEST.json", {"schema": "zerorun.controlled-handoff-export.v1",
        "files": rows, "reconciliation": summary, "excluded_directory_names": sorted(BLOCKED),
        "includes_credentials": False, "includes_workspaces": False,
        "input_archives_and_metadata": "separate acquisition artifact; case input hashes remain in protocol"})
    validate_saved(destination)
    return {"files": len(rows), "bytes": sum(r["bytes"] for r in rows), "destination": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(export(args.source, args.destination))


if __name__ == "__main__":
    main()
