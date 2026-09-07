"""Freeze, select and anonymously acquire inert SWE-rebench handoff sources."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
import urllib.request

DATASET = "nebius/SWE-rebench"
REVISION = "89cdfbab4ab1bd8f5a658bb212d1b63624f4f881"
TOTAL_ROWS = 21336
SEED = "zerorun-handoff-acquisition-v1"
SHORTLIST = (
    "pallets/click", "pypa/packaging", "tartley/colorama", "eliben/pycparser",
    "joke2k/django-environ", "python-humanize/humanize", "tobymao/sqlglot",
    "terryyin/lizard", "eyeseast/python-frontmatter", "joshtemple/lkml",
    "psf/requests", "python-attrs/attrs", "more-itertools/more-itertools",
    "pallets/itsdangerous", "pallets/markupsafe", "mahmoud/boltons",
    "tkem/cachetools", "dbader/schedule", "lepture/mistune", "rspeer/python-ftfy",
)
PARQUETS = ("data/test-00000-of-00002.parquet", "data/test-00001-of-00002.parquet")
ARCHIVE_LIMIT = 64 * 1024 * 1024
PARQUET_LIMIT = 512 * 1024 * 1024
METADATA_LIMIT = 4 * 1024 * 1024
PATCH_LIMIT = 2 * 1024 * 1024


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def save_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": path.name, "bytes": len(raw), "sha256": sha(raw)}


def save_json(output, relative, value):
    result = save_new(output / relative, encoded(value))
    result["path"] = relative
    return result


def safe_target(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("unsafe test target")
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in {"", ".", "..", ".git", ".zerorun"} for part in value.split("/"))
            or any(part.startswith("-") for part in path.parts)
            or any(char.isspace() for char in value) or ":" in value):
        raise ValueError("unsafe test target")
    return value


def patch_targets(patch):
    if not isinstance(patch, str) or not patch.strip() or len(patch.encode("utf-8")) > PATCH_LIMIT:
        raise ValueError("empty, non-string, or oversized test patch")
    if "diff --git " not in patch or "@@ " not in patch:
        raise ValueError("test patch is not a text unified git diff")
    targets = []
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        value = line[4:]
        if value == "/dev/null":
            continue
        if not value.startswith("b/"):
            raise ValueError("test patch has an unsupported destination")
        value = safe_target(value[2:])
        if value.endswith(".py"):
            targets.append(value)
    if not targets:
        raise ValueError("test patch contains no Python-file target")
    if len(set(targets)) > 16:
        raise ValueError("test patch exceeds sixteen Python-file targets")
    return sorted(set(targets))


def validate_case(index, row):
    if not isinstance(row, dict) or row.get("repo") not in SHORTLIST:
        raise ValueError("not a shortlisted row")
    case_id = row.get("instance_id")
    if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,240}", case_id):
        raise ValueError("invalid instance identifier")
    base = row.get("base_commit")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("base commit is not exact lowercase SHA-1")
    license_name = row.get("license_name")
    if not isinstance(license_name, str) or not license_name.strip():
        raise ValueError("missing repository license label")
    patch = row.get("patch")
    if not isinstance(patch, str) or not patch.strip() or len(patch.encode("utf-8")) > PATCH_LIMIT:
        raise ValueError("empty, non-string, or oversized source patch")
    if "diff --git " not in patch or "@@ " not in patch:
        raise ValueError("source patch is not a text unified git diff")
    targets = patch_targets(row.get("test_patch"))
    if len(encoded(row)) > METADATA_LIMIT:  # No coercion of non-JSON/nonfinite values.
        raise ValueError("case metadata exceeds frozen byte bound")
    return {"case_id": case_id, "repo": row["repo"], "base_commit": base,
            "row_index": index, "targets": targets, "row": row}


def order(case, phase):
    value = f"{SEED}:{phase}:{case['repo']}:{case['case_id']}:{case['base_commit']}"
    return (sha(value.encode("utf-8")), case["case_id"])


def select(rows):
    candidates = [(index, row) for index, row in enumerate(rows)
                  if isinstance(row, dict) and row.get("repo") in SHORTLIST]
    counts = Counter(row.get("instance_id") for _, row in candidates
                     if isinstance(row.get("instance_id"), str))
    valid = {repo: [] for repo in SHORTLIST}
    invalid = []
    for index, row in candidates:
        try:
            if isinstance(row.get("instance_id"), str) and counts[row["instance_id"]] != 1:
                raise ValueError("duplicate candidate instance identifier")
            case = validate_case(index, row)
            valid[case["repo"]].append(case)
        except (ValueError, TypeError, UnicodeError) as exc:
            invalid.append({"row_index": index, "repo": row.get("repo"),
                            "case_id": row.get("instance_id"), "reason": str(exc)})
    eligible = [repo for repo in SHORTLIST if len(valid[repo]) >= 3]
    selected_repos = eligible[:10]
    main = [case for repo in selected_repos for case in sorted(valid[repo], key=lambda c: order(c, "main"))[:3]]
    main_ids = {case["case_id"] for case in main}
    spare = [case for repo in selected_repos for case in valid[repo] if case["case_id"] not in main_ids]
    pilot = sorted(spare, key=lambda c: order(c, "pilot"))[:2]
    ledger = [{"repo": repo, "candidate_count": sum(row.get("repo") == repo for _, row in candidates),
               "valid_count": len(valid[repo]), "selected": repo in selected_repos,
               "reason": "selected" if repo in selected_repos else "fewer_than_three_valid_cases"
               if len(valid[repo]) < 3 else "after_first_ten_eligible_repositories"} for repo in SHORTLIST]
    return {"main": main, "pilot": pilot, "repository_ledger": ledger,
            "invalid_cases": invalid, "candidate_rows": len(candidates),
            "selection_complete": len(selected_repos) == 10 and len(main) == 30 and len(pilot) == 2}


def download(output, relative, url, limit):
    path = output / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"url": url, "path": relative, "attempts": 1, "started_utc": utc(), "limit_bytes": limit}
    start = time.monotonic()
    digest = hashlib.sha256()
    count = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ZeroRun-source-acquisition/1.0"})
        with path.open("xb") as stream, urllib.request.urlopen(request, timeout=45) as response:
            record["response_headers"] = {key.lower(): value for key, value in response.headers.items()
                                           if key.lower() in {"content-type", "content-length", "etag", "x-repo-commit", "date"}}
            while True:
                if time.monotonic() - start > 180:
                    raise TimeoutError("download exceeded 180-second wall limit")
                chunk = response.read(min(1024 * 1024, limit - count + 1))
                if not chunk:
                    break
                if count + len(chunk) > limit:
                    raise ValueError("download exceeds frozen byte bound")
                stream.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        if count == 0:
            raise ValueError("empty download")
        record["status"] = "ok"
    except Exception as exc:
        record.update(status="unavailable", error=f"{type(exc).__name__}: {exc}")
    record.update(bytes=count, sha256=digest.hexdigest(), completed_utc=utc(),
                  elapsed_seconds=round(time.monotonic() - start, 6))
    save_json(output, relative + ".download.json", record)
    if record["status"] != "ok":
        raise RuntimeError(f"download unavailable: {relative}: {record['error']}")
    return {"path": relative, "bytes": count, "sha256": digest.hexdigest()}


def protocol_plan():
    here = Path(__file__).resolve().parent
    return {"schema": "zerorun.handoff-acquisition-plan.v1", "started_utc": utc(),
            "dataset": DATASET, "revision": REVISION, "config": "default", "split": "test",
            "total_rows": TOTAL_ROWS, "license": "CC-BY-4.0", "seed": SEED,
            "shortlist": list(SHORTLIST), "shortlist_basis": "disclosed convenience/support, not measured speed",
            "parquet_files": list(PARQUETS), "main_repositories": 10, "main_cases_per_repository": 3,
            "pilot_cases": 2, "pilot_rule": "two seeded cases remaining in selected repositories",
            "source_sha256": sha(Path(__file__).read_bytes()),
            "protocol_sha256": sha((here / "PROTOCOL.md").read_bytes()),
            "archive_limit_bytes": ARCHIVE_LIMIT, "parquet_limit_bytes": PARQUET_LIMIT,
            "metadata_limit_bytes": METADATA_LIMIT, "patch_limit_bytes": PATCH_LIMIT,
            "attempts_per_url": 1, "download_deadline_between_reads_seconds": 180,
            "socket_timeout_seconds": 45, "selection_uses_test_outcomes": False,
            "code_executed": False, "archives_extracted": False}


def collect(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = save_json(output, "plan.json", protocol_plan())  # Before any network request.
    receipt = {"schema": "zerorun.handoff-acquisition.v1", "plan": plan, "completed": False,
               "code_executed": False, "archives_extracted": False}
    try:
        meta = download(output, "dataset-before.json", f"https://huggingface.co/api/datasets/{DATASET}", METADATA_LIMIT)
        before = json.loads((output / meta["path"]).read_bytes())
        if before.get("sha") != REVISION or before.get("cardData", {}).get("license") != "cc-by-4.0":
            raise ValueError("dataset revision or license differs")
        receipt["metadata_before"] = meta
        receipt["source_notice"] = download(output, "source-notices/README.md",
            f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/README.md", METADATA_LIMIT)
        import pyarrow
        import pyarrow.parquet as parquet
        if pyarrow.__version__ != "23.0.1":
            raise ValueError("collector requires pyarrow 23.0.1")
        receipt["pyarrow_version"] = pyarrow.__version__
        raw_records = []
        rows = []
        for relative in PARQUETS:
            entry = download(output, relative, f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{relative}", PARQUET_LIMIT)
            raw_records.append(entry)
            # Preserve global row indices without retaining unrelated patches.
            # Bounded batches avoid materializing the full corpus in VM memory.
            for batch in parquet.ParquetFile(output / relative).iter_batches(batch_size=128):
                rows.extend(row if row.get("repo") in SHORTLIST else None for row in batch.to_pylist())
        receipt["parquets"] = raw_records
        if len(rows) != TOTAL_ROWS:
            raise ValueError(f"row total differs: {len(rows)}")
        selection = select(rows)
        sealed = {key: value for key, value in selection.items() if key not in {"main", "pilot"}}
        sealed.update({phase: [{key: value for key, value in case.items() if key != "row"}
                               for case in selection[phase]] for phase in ("pilot", "main")})
        receipt["selection"] = save_json(output, "selection.json", sealed)
        manifests = {}
        for phase in ("pilot", "main"):
            cases = []
            for case in selection[phase]:
                item = {key: case[key] for key in ("case_id", "repo", "base_commit", "targets")}
                prefix = f"cases/{case['case_id']}"
                wrapper = {"dataset": DATASET, "revision": REVISION, "config": "default", "split": "test", "partial": False,
                           "rows": [{"row_idx": case["row_index"], "row": case["row"], "truncated_cells": []}],
                           "metadata_origin": "one complete row decoded from retained revision-pinned parquet"}
                item["metadata"] = save_json(output, prefix + "/metadata.json", wrapper)
                try:
                    item["source_archive"] = download(output, prefix + "/source.tar.gz",
                        f"https://codeload.github.com/{case['repo']}/tar.gz/{case['base_commit']}", ARCHIVE_LIMIT)
                except Exception as exc:
                    item["source_archive"] = None
                    item["acquisition_error"] = str(exc)
                    item["disposition"] = "UNAVAILABLE_ACQUISITION"
                    item["error"] = str(exc)
                cases.append(item)
                print(json.dumps({"phase": phase, "case_id": item["case_id"], "archive_available": item["source_archive"] is not None}), flush=True)
            manifest = {"schema": "zerorun.handoff-selection.v1", "phase": phase,
                        "plan": plan, "selection": receipt["selection"], "cases": cases,
                        "selection_complete": selection["selection_complete"],
                        "acquisition_complete": selection["selection_complete"] and all(case["source_archive"] for case in cases)}
            manifests[phase] = save_json(output, phase + ".json", manifest)
        receipt["manifests"] = manifests
        receipt["completed"] = selection["selection_complete"] and all(
            json.loads((output / record["path"]).read_bytes())["acquisition_complete"] for record in manifests.values())
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    receipt["completed_utc"] = utc()
    save_json(output, "receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.output)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
