"""Record-only export, pruning workspaces, dependency files and registry store."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1.export import BLOCKED
from .validate import validate_image, validate_saved

EXCLUDED = BLOCKED | {"context", "registry-store"}


def export(source, destination, image_build=None):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve(strict=False)
    h.require(not destination.exists() and not destination.is_relative_to(source), "new external export destination required")
    summary = validate_image(source) if image_build is None else validate_saved(source, image_build)
    candidates = []
    for current, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in dirs:
            h.require(not (Path(current) / name).is_symlink(), "export directory link refused")
        for name in sorted(files):
            path = Path(current) / name
            h.require(path.suffix in {".json", ".py", ".md", ".log"}, "unexpected record type")
            raw = h.ordinary(path)
            candidates.append((path.relative_to(source), raw))
    destination.mkdir(parents=True)
    rows = []
    for relative, raw in candidates:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        rows.append({"path": relative.as_posix(), "bytes": len(raw), "sha256": h.sha(raw)})
    h.save(destination / "EXPORT_MANIFEST.json", {"schema": "zerorun.image-handoff-export.v2", "files": rows,
        "reconciliation": summary, "excluded_directory_names": sorted(EXCLUDED),
        "credentials_included": False, "registry_or_image_archive_included": False})
    validate_image(destination) if image_build is None else validate_saved(destination, image_build)
    return {"files": len(rows), "bytes": sum(r["bytes"] for r in rows)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    p.add_argument("--image-build", type=Path)
    args = p.parse_args()
    print(export(args.source, args.destination, args.image_build))


if __name__ == "__main__":
    main()
