"""Bind exact experiment bytes to main, disclosing physical newline differences."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", args.commit + "^{commit}"], cwd=ROOT, text=True).strip()
    rows = []
    archive = ROOT / "research/sqj/source-final"
    for path in sorted(archive.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(archive).as_posix()
        raw = path.read_bytes()
        git_bytes = subprocess.check_output(["git", "show", commit + ":" + relative], cwd=ROOT)
        equal = raw == git_bytes
        if not equal and raw.replace(b"\r\n", b"\n") != git_bytes.replace(b"\r\n", b"\n"):
            raise ValueError("non-newline difference between main and experiment: " + relative)
        rows.append({"path": relative, "experiment_sha256": hashlib.sha256(raw).hexdigest(),
                     "commit_blob_sha256": hashlib.sha256(git_bytes).hexdigest(),
                     "byte_identical": equal, "only_physical_newline_difference": not equal})
    payload = {"main_commit": commit, "files": rows, "all_files_bound": True,
               "note": "Experiment and package receipts bind their exact original bytes; a Git commit alone is not substituted for those hashes."}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
    print(json.dumps({"commit": commit, "files": len(rows), "newline_only_differences": sum(not r["byte_identical"] for r in rows)}))


if __name__ == "__main__":
    main()
