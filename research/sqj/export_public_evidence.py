"""Allowlisted VM evidence export; never include private cache keys or test state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile

EXCLUDED = {"private-cache-authentication-NOT-FOR-PUBLICATION", "pytest-temp", "testmon-runtime", "testmon-state", "workspaces"}
ALLOWED_SUFFIXES = {".json", ".xml", ".log", ".txt", ".py"}
ALLOWED_ROOTS = {"campaign-1", "linux-regression-1", "cleanup-and-venv-linux", "readonly-hit-linux",
                 "linux-regression-final", "whole-task-pilot-1", "whole-task-pilot-2", "whole-task-pilot-3",
                 "comparison-pilot-1", "comparison-pilot-2", "comparison-final-1", "comparison-packaging-corrected",
                 "comparison-packaging-final", "comparison-more-whole-task"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--select", nargs="*", choices=sorted(ALLOWED_ROOTS))
    args = parser.parse_args()
    root = args.results.resolve(strict=True)
    if args.output.exists():
        parser.error("refusing to overwrite an evidence archive")
    chosen = set(args.select or ALLOWED_ROOTS)
    files = []
    for name in sorted(chosen):
        directory = root / name
        if not directory.exists():
            continue
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
            for entry in [*dirs, *sorted(names)]:
                path = Path(parent) / entry
                relative = path.relative_to(root)
                if path.is_symlink():
                    raise RuntimeError("link-like evidence cannot be exported: " + str(relative))
                if path.is_file() and path.suffix in ALLOWED_SUFFIXES:
                    if path.stat().st_size > 80 * 1024 * 1024:
                        raise RuntimeError("evidence exceeds per-file bound: " + str(relative))
                    files.append(path)
        driver = root / (name + "-driver.log")
        if driver.is_file() and not driver.is_symlink():
            files.append(driver)
    manifest = []
    with tarfile.open(args.output, "x:gz") as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            manifest.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            archive.add(path, arcname=relative, recursive=False)
    receipt = {"files": manifest, "excluded_directory_names": sorted(EXCLUDED),
               "archive_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    receipt_path = args.output.with_name(args.output.name + ".json")
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
    print(json.dumps({"files": len(files), "archive": str(args.output), "sha256": receipt["archive_sha256"]}))


if __name__ == "__main__":
    main()
