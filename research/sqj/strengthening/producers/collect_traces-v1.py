"""Bounded anonymous collection of a predetermined licensed trace sample.

Downloaded strings are inert data. This module never executes trajectory actions.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

DATASET = "nebius/SWE-rebench-openhands-trajectories"
REVISION = "35455389ab51bf5e2306bfd436ef72d0f98bf882"
TOTAL = 67074
SEED = "zerorun-openhands-observation-v1"
MAX_BYTES = 16 * 1024 * 1024


def indices(mode):
    if mode == "pilot":
        return list(range(10))
    if mode != "cohort":
        raise ValueError("unknown selection mode")
    ranked = sorted(range(10, TOTAL), key=lambda i: hashlib.sha256(f"{SEED}:{i}".encode()).digest())
    return sorted(ranked[:128])


def utc():
    return datetime.now(timezone.utc).isoformat()


def save_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def save_json(path, value):
    save_new(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def fetch(url):
    last = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ZeroRun-research-data-audit/1.0"})
            with urllib.request.urlopen(request, timeout=40) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("response exceeds fixed 16 MiB bound")
            return raw, attempt + 1
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed after 3 attempts: {type(last).__name__}: {last}")


def metadata(output, label):
    url = "https://huggingface.co/api/datasets/" + DATASET
    raw, attempts = fetch(url)
    save_new(output / f"metadata-{label}.json", raw)
    data = json.loads(raw)
    if data.get("sha") != REVISION or data.get("cardData", {}).get("license") != "cc-by-4.0":
        raise ValueError("dataset revision/license differs from frozen observation")
    return {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "revision": data["sha"], "attempts": attempts}


def collect_row(index, output):
    params = urllib.parse.urlencode({"dataset": DATASET, "config": "default", "split": "train", "offset": index, "length": 1})
    url = "https://datasets-server.huggingface.co/rows?" + params
    path = Path("rows") / f"row-{index:05d}.json"
    entry = {"row_index": index, "url": url, "path": path.as_posix(), "requested_utc": utc()}
    try:
        raw, attempts = fetch(url)
        save_new(output / path, raw)
        data = json.loads(raw)
        if data.get("num_rows_total") != TOTAL:
            raise ValueError("dataset row count changed")
        rows = data.get("rows", [])
        if len(rows) != 1 or rows[0].get("row_idx") != index:
            raise ValueError("response row identity mismatch")
        entry.update(status="ok", sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), attempts=attempts,
                     truncated_cells=rows[0].get("truncated_cells", []))
    except Exception as error:
        entry.update(status="unavailable", error=f"{type(error).__name__}: {error}")
        if (output / path).is_file():
            raw = (output / path).read_bytes()
            entry.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    entry["completed_utc"] = utc()
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pilot", "cohort"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = indices(args.mode)
    args.output.mkdir(parents=True, exist_ok=False)
    protocol = {"schema": "zerorun.trace-collection.v1", "dataset": DATASET, "observed_revision": REVISION,
                "license": "CC-BY-4.0", "selection_mode": args.mode, "selection_seed": SEED,
                "population_rows": TOTAL, "selected_indices": selected, "started_utc": utc(),
                "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "raw_response_limit_bytes": MAX_BYTES, "workers": 4,
                "selection_uses_outcomes_or_repeat_counts": False,
                "immutable_revision_specific_endpoint_claimed": False}
    save_json(args.output / "selection.json", protocol)
    before = metadata(args.output, "before")
    for filename in ("README.md", "LICENSE"):
        url = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{filename}"
        raw, _ = fetch(url)
        save_new(args.output / "source-notices" / filename, raw)
    entries = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(collect_row, i, args.output) for i in selected]
        for future in as_completed(futures):
            entry = future.result()
            entries.append(entry)
            print(json.dumps({"completed": len(entries), "of": len(selected), "row": entry["row_index"], "status": entry["status"]}), flush=True)
    after = metadata(args.output, "after")
    result = {**protocol, "completed_utc": utc(), "metadata_before": before, "metadata_after": after,
              "entries": sorted(entries, key=lambda e: e["row_index"]),
              "successful_downloads": sum(e["status"] == "ok" for e in entries),
              "unavailable_downloads": sum(e["status"] != "ok" for e in entries)}
    save_json(args.output / "collection.json", result)
    print(json.dumps({k: result[k] for k in ("selection_mode", "successful_downloads", "unavailable_downloads")}), flush=True)


if __name__ == "__main__":
    main()
