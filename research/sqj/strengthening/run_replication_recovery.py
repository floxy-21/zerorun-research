"""Explicit post-crash amendment: retry only unfinished preselected blocks.

The original protocol and every original observation remain unchanged. This
is NOT an uninterrupted completion of its no-retry protocol. New attempts use
the byte-identical frozen driver and independent worktrees/cache fixtures.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from research.sqj.strengthening import randomized_replication as frozen

DRIVER_SHA = "eaa420f858dc07349a42ae6be36aba795ba9d57387d5be795e6e3552f5ca0a4a"


def evidence_inventory(root):
    """Hash public receipts only; authentication fixtures are never published."""
    records = []
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root)
        if any(p in {"private-cache-authentication-NOT-FOR-PUBLICATION", "workspace", "workspaces", ".zerorun", ".zerorun-env"} for p in relative.parts):
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("nonordinary receipt")
        records.append({"path": relative.as_posix(), "sha256": frozen.sha256_file(path), "bytes": path.stat().st_size})
    return records


def plan_recovery(original, protocol):
    if protocol["producer_sha256"] != DRIVER_SHA or protocol["schedule"] != frozen.schedule():
        raise ValueError("original producer or schedule changed")
    if (original / "campaign-summary.json").exists():
        raise ValueError("recovery requires interrupted campaign without final receipt")
    pending, complete, interrupted = [], [], []
    gap = False
    for subject in protocol["schedule"]:
        blocks = []
        for block in subject["trajectories"]:
            path = original / subject["workload"] / f"trajectory-{block['trajectory']}"
            receipt = path / "block-summary.json"
            if receipt.exists():
                value = json.loads(receipt.read_text())
                if value.get("completed") is not True or value.get("requests") != 7:
                    raise ValueError("recorded failed block cannot be reclassified as host interruption")
                if gap:
                    raise ValueError("completed original block after interruption")
                complete.append({"workload": subject["workload"], **block})
            else:
                gap = True
                blocks.append(block)
                if path.exists():
                    # A mismatch already recorded before the host interruption
                    # must not be silently retried as an infrastructure error.
                    for observation in path.glob("request-*/observation.json"):
                        try:
                            row = json.loads(observation.read_text())
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            # An incomplete crash-flush is not an observation.
                            # Preserve its exact bytes in the input inventory;
                            # independent analysis must report it as malformed,
                            # never infer agreement or a zero-duration oracle.
                            continue
                        if not row.get("whole_task_exit_agreement") or not row.get("cache_behavior_expected"):
                            raise ValueError("original material mismatch prohibits recovery")
                    interrupted.append({"workload": subject["workload"], **block})
        if blocks:
            pending.append({"workload": subject["workload"], "trajectories": blocks})
    if not pending or not interrupted:
        raise ValueError("no positively observed interrupted block")
    return {"completed_original_blocks": complete, "interrupted_original_blocks": interrupted,
            "recovery_schedule": pending}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine", "workloads", "original", "incident-file", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("requires existing reviewed POSIX Docker laboratory")
    engine = args.engine.resolve(strict=True)
    original = args.original.resolve(strict=True)
    incident = args.incident_file.resolve(strict=True)
    if not incident.is_file() or incident.is_symlink():
        raise ValueError("ordinary external incident receipt required")
    if frozen.sha256_file(Path(frozen.__file__)) != DRIVER_SHA:
        raise ValueError("frozen driver changed")
    sys.path.insert(0, str(engine))
    from tools import product_generalization_benchmark as bench
    from zerorun import api, hermetic, oci
    from zerorun.manifest import load_manifest
    cohort = frozen.load_module(frozen.SQJ / "run_frozen_campaign.py", "frozen_recovery_cohort")
    helper = frozen.load_recovery()
    previous = json.loads((original / "protocol.json").read_text())
    planned = plan_recovery(original, previous)
    frozen.validate_limits(bench, oci)
    protected = frozen.protected_source_identity(engine)
    if protected != previous["protected_source_identity"] or bench._capture_source_identity() != previous["engine_identity"]:
        raise ValueError("source differs from interrupted campaign")
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    incident_raw = incident.read_bytes()
    (output / "external-incident.txt").write_bytes(incident_raw)
    protocol = {"schema": "zerorun.short-replication-recovery-protocol.v1", "frozen_utc": frozen.utc_now(),
        "producer_sha256": frozen.sha256_file(Path(__file__)), "frozen_driver_sha256": DRIVER_SHA,
        "original_protocol_sha256": frozen.sha256_file(original / "protocol.json"),
        "original_evidence_inventory": evidence_inventory(original),
        "incident_sha256": frozen.sha256_file(output / "external-incident.txt"),
        "protected_source_before": protected, "engine_identity_before": bench._capture_source_identity(),
        "host_before": frozen.host_snapshot(), **planned,
        "amendment": "Host interrupted original no-retry protocol. Preserve original partial attempts and retry their entire preselected block once; never substitute this for uninterrupted execution.",
        "selection_uses_performance": False, "operator_authority_allowed": False,
        "original_setup_layer_cost_if_unrecorded": "unavailable; do not invent or issue setup-inclusive aggregate"}
    frozen.save(output / "recovery-protocol.json", protocol)
    private = output / "private-cache-authentication-NOT-FOR-PUBLICATION"
    private.mkdir(mode=0o700)
    results = []
    with patch.dict(os.environ, {"ZERORUN_TRUST_ROOT": str(private), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}):
        for subject in planned["recovery_schedule"]:
            item = next(row for row in cohort.WORKLOADS if row[0] == subject["workload"])
            try:
                value = frozen.run_workload(bench, api, hermetic, load_manifest, helper, item,
                    (args.workloads / item[0]).resolve(strict=True), output / item[0], cohort.IMAGE, subject["trajectories"])
                done = len(value["blocks"]) == len(subject["trajectories"]) and all(b["completed"] for b in value["blocks"]) and value["source_stable"]
                results.append({"workload": item[0], "completed": done, "requests": len(value["rows"])})
            except Exception as error:
                failure = {"workload": item[0], "completed": False, "error_type": type(error).__name__, "error": str(error), "retry_attempted": False}
                frozen.save(output / (item[0] + "-failure.json"), failure)
                results.append(failure)
                break
    after = frozen.protected_source_identity(engine)
    unchanged = evidence_inventory(original) == protocol["original_evidence_inventory"]
    authorities = list(private.glob("repositories/*/authorities/*.json"))
    completed = len(results) == len(planned["recovery_schedule"]) and all(row["completed"] for row in results) and after == protected and unchanged and not authorities
    final = {"schema": "zerorun.short-replication-recovery-completion.v1", "completed_utc": frozen.utc_now(),
        "recovery_protocol_sha256": frozen.sha256_file(output / "recovery-protocol.json"), "results": results,
        "protected_source_after": after, "engine_identity_after": bench._capture_source_identity(),
        "original_evidence_unchanged": unchanged, "operator_authority_receipts_created": bool(authorities),
        "completed": completed, "uninterrupted_original_campaign": False, "host_after": frozen.host_snapshot()}
    frozen.save(output / "recovery-completion.json", final)
    print(json.dumps(final, sort_keys=True), flush=True)
    return int(not completed)


if __name__ == "__main__":
    raise SystemExit(main())
