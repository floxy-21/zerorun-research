"""Recover only the known pre-selection second-parquet timeout, without retries."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import time
import urllib.request
from unittest.mock import patch

from research.softwarex.handoff_acquisition_v1 import collect as original

HERE = Path(__file__).resolve().parent
DEADLINE_SECONDS = 600
SOCKET_SECONDS = 45
CHUNK = 1024 * 1024
FIRST, SECOND = original.PARQUETS
REUSED = ("dataset-before.json", "source-notices/README.md", FIRST)
SMALL = ("plan.json", "receipt.json", "dataset-before.json.download.json",
         "source-notices/README.md.download.json", FIRST + ".download.json", SECOND + ".download.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite JSON value")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    original.encoded(value)
    return value


def file_record(base, relative, limit=original.PARQUET_LIMIT):
    parts = relative.split("/")
    require(relative and all(p not in {"", ".", ".."} for p in parts)
            and not any(c in relative for c in "\\:\x00"), "unsafe relative input")
    path = base
    for part in parts:
        path /= part
        info = path.lstat()
        require(not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400,
                "linked acquisition input")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit, "nonregular or oversized input")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            count += len(chunk)
            require(count <= limit, "input grew beyond limit")
            digest.update(chunk)
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            and count == before.st_size, "input changed during hashing")
    return {"path": relative, "bytes": count, "sha256": digest.hexdigest()}


def read(base, relative):
    file_record(base, relative, original.METADATA_LIMIT)
    return strict((base / relative).read_bytes())


def prior_inventory(prior):
    require(prior.is_dir() and not prior.is_symlink(), "prior acquisition directory required")
    require(not any((prior / name).exists() for name in ("selection.json", "pilot.json", "main.json")),
            "recovery is restricted to a pre-selection failure")
    plan = read(prior, "plan.json")
    expected = original.protocol_plan()
    require({k: v for k, v in plan.items() if k != "started_utc"}
            == {k: v for k, v in expected.items() if k != "started_utc"}, "original frozen plan/source differs")
    receipt = read(prior, "receipt.json")
    require(receipt.get("schema") == "zerorun.handoff-acquisition.v1"
            and receipt.get("completed") is False and receipt.get("code_executed") is False
            and receipt.get("archives_extracted") is False, "original failure receipt differs")
    require(receipt.get("plan") == file_record(prior, "plan.json", original.METADATA_LIMIT), "original plan binding differs")
    require(not any(key in receipt for key in ("selection", "manifests")), "receipt already records selection")
    records = {name: file_record(prior, name, original.METADATA_LIMIT) for name in SMALL}
    for name in (*REUSED, SECOND):
        limit = original.PARQUET_LIMIT if name in original.PARQUETS else original.METADATA_LIMIT
        actual = file_record(prior, name, limit)
        download = read(prior, name + ".download.json")
        require(download.get("path") == name and type(download.get("bytes")) is int
                and download["bytes"] == actual["bytes"] and download.get("sha256") == actual["sha256"]
                and download.get("attempts") == 1 and download.get("limit_bytes") == limit,
                "original download bytes or scope differ: " + name)
        if name in original.PARQUETS:
            require(download.get("url") == parquet_url(name), "original parquet URL differs")
        if name == SECOND:
            require(download.get("status") == "unavailable"
                    and download.get("error") == "TimeoutError: download exceeded 180-second wall limit"
                    and SECOND in receipt.get("error", "") and "180-second wall limit" in receipt["error"],
                    "only the original second-parquet deadline failure may be recovered")
        else:
            require(download.get("status") == "ok" and actual["bytes"] > 0,
                    "reused input was not a complete original download")
        records[name] = actual
    require(receipt.get("metadata_before") == records["dataset-before.json"]
            and receipt.get("source_notice") == records["source-notices/README.md"], "original metadata/notice receipt differs")
    metadata = read(prior, "dataset-before.json")
    require(metadata.get("sha") == original.REVISION
            and metadata.get("cardData", {}).get("license") == "cc-by-4.0", "recorded dataset revision/license differs")
    return [records[name] for name in sorted(records)]


def parquet_url(relative):
    return f"https://huggingface.co/datasets/{original.DATASET}/resolve/{original.REVISION}/{relative}"


def copy_bound(prior, output, row, target=None):
    target = target or row["path"]
    destination = output / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, count = hashlib.sha256(), 0
    with (prior / row["path"]).open("rb") as source, destination.open("xb") as sink:
        while chunk := source.read(CHUNK):
            count += len(chunk)
            require(count <= row["bytes"], "copied input grew")
            digest.update(chunk)
            sink.write(chunk)
    require(count == row["bytes"] and digest.hexdigest() == row["sha256"], "copied input changed")
    return {"path": target, "bytes": count, "sha256": digest.hexdigest()}


def second_download(output, relative, url, limit):
    require(relative == SECOND and url == parquet_url(SECOND) and limit == original.PARQUET_LIMIT,
            "600-second amendment applies only to the second pinned parquet")
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {"url": url, "path": relative, "attempts": 1, "started_utc": original.utc(),
              "limit_bytes": limit, "deadline_between_reads_seconds": DEADLINE_SECONDS,
              "socket_timeout_seconds": SOCKET_SECONDS, "resume_used": False,
              "initial_attempt_preserved": True}
    digest, count, start = hashlib.sha256(), 0, time.monotonic()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ZeroRun-source-acquisition-recovery/1.0"})
        with destination.open("xb") as sink, urllib.request.urlopen(request, timeout=SOCKET_SECONDS) as response:
            require(response.status == 200, "full immutable download requires HTTP 200")
            result["response_headers"] = {key.lower(): value for key, value in response.headers.items()
                if key.lower() in {"content-type", "content-length", "etag", "x-repo-commit", "date"}}
            length = result["response_headers"].get("content-length")
            if length is not None:
                require(length.isdigit() and 0 < int(length) <= limit, "invalid or oversized content length")
            while True:
                if time.monotonic() - start > DEADLINE_SECONDS:
                    raise TimeoutError("download exceeded 600-second wall limit")
                chunk = response.read(min(CHUNK, limit - count + 1))
                if not chunk:
                    break
                require(count + len(chunk) <= limit, "download exceeds frozen byte bound")
                sink.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        require(count >= 8 and (length is None or count == int(length)), "empty/truncated content-length response")
        with destination.open("rb") as stream:
            beginning = stream.read(4)
            stream.seek(-4, 2)
            ending = stream.read(4)
        require(beginning == ending == b"PAR1", "download is not a complete parquet container")
        result.update(status="ok", parquet_container_magic_ok=True)
    except Exception as exc:
        result.update(status="unavailable", error=f"{type(exc).__name__}: {exc}")
    result.update(bytes=count, sha256=digest.hexdigest(), completed_utc=original.utc(),
                  elapsed_seconds=round(time.monotonic() - start, 6))
    original.save_json(output, relative + ".download.json", result)
    if result["status"] != "ok":
        raise RuntimeError(f"download unavailable: {relative}: {result['error']}")
    return {"path": relative, "bytes": count, "sha256": digest.hexdigest()}


def recover(prior, output):
    prior, output = prior.resolve(), output.absolute()
    require(not output.exists() and not output.is_relative_to(prior), "new output outside prior attempt required")
    initial = prior_inventory(prior)
    indexed = {row["path"]: row for row in initial}
    frozen_plan = original.protocol_plan()
    amendment_sources = [file_record(HERE, name, original.METADATA_LIMIT)
                         for name in ("__init__.py", "PROTOCOL.md", "recover.py")]
    frozen_plan["transport_recovery"] = {
        "schema": "zerorun.handoff-acquisition-transport-recovery.v1",
        "amendment_sources": amendment_sources, "initial_directory": str(prior),
        "initial_attempt_files": initial, "initial_failure_retained": True,
        "second_parquet_deadline_between_reads_seconds": DEADLINE_SECONDS,
        "resume_used": False, "selection_logic_changed": False, "archive_limits_changed": False}
    old_download = original.download
    archived_initial = False

    def adapted_download(destination, relative, url, limit):
        nonlocal archived_initial
        if not archived_initial:
            for name in SMALL:
                copy_bound(prior, destination, indexed[name], "initial-attempt/" + name)
            archived_initial = True
        if relative in REUSED:
            require(file_record(prior, relative, limit) == indexed[relative], "reused input changed after freeze")
            row = copy_bound(prior, destination, indexed[relative])
            original.save_json(destination, relative + ".download.json", {
                "status": "reused_complete_original", "url": url, **row,
                "network_attempts_this_recovery": 0, "original_download_record": "initial-attempt/" + relative + ".download.json"})
            return row
        if relative == SECOND:
            return second_download(destination, relative, url, limit)
        require(relative.startswith("cases/") and relative.endswith("/source.tar.gz")
                and limit == original.ARCHIVE_LIMIT, "unexpected recovery network request")
        return old_download(destination, relative, url, limit)

    with patch.object(original, "protocol_plan", lambda: deepcopy(frozen_plan)), patch.object(original, "download", adapted_download):
        receipt = original.collect(output)
    unchanged = prior_inventory(prior) == initial
    require([file_record(HERE, row["path"], original.METADATA_LIMIT) for row in amendment_sources] == amendment_sources,
            "recovery source changed during acquisition")
    recovery = {"schema": "zerorun.handoff-acquisition-transport-recovery-completion.v1",
                "completed": receipt["completed"] and unchanged,
                "original_failure_retained": True, "original_inputs_unchanged": unchanged,
                "original_receipt": indexed["receipt.json"], "new_receipt": file_record(output, "receipt.json"),
                "new_plan": file_record(output, "plan.json"), "selection_logic_changed": False,
                "code_executed": False, "archives_extracted": False, "completed_utc": original.utc()}
    original.save_json(output, "recovery-completion.json", recovery)
    require(unchanged, "original attempt changed during recovery")
    return recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = recover(args.prior, args.output)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["completed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
