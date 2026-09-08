"""Reconcile the separate producer, installed consumer and assisted-repair evidence.

Only saved, sealed records are read. No model, test, subprocess, authorization,
or application code is executed. Internal prose review remains explicitly
separate from machine-checked provenance and from independent human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "research/softwarex/evidence/"
ORACLES = PREFIX + "agent-application-053-oracles-v2/record-only"
CONSUMERS = PREFIX + "agent-application-053-consumers-v2/record-only"
SUPPLEMENT = PREFIX + "agent-application-053-sqlglot-supplement-v1/record-only"
REVIEW = PREFIX + "agent-application-053-consumer-review-v1.json"
OUTPUT = "research/softwarex/generated/agent-application-053-v1.json"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    from research.softwarex.agent_application_053 import validation as v
    return v.strict(v.real(path).read_bytes())


def review_consumer_messages(root, campaign):
    """Bind every assessment to the exact previously reconciled model response."""
    from research.softwarex.agent_application_053.validation import STAGES
    value = read(root / REVIEW)
    require(value["schema"] == "zerorun.real-consumer-internal-review.0.5.3.v1"
            and value["record_manifest_sha256"] == campaign["record_manifest_sha256"]
            and value["independent_human_review"] is False
            and isinstance(value["method"], str) and value["method"].strip(),
            "consumer review provenance differs")
    expected = [(case["case_id"], stage) for case in campaign["cases"] for stage in STAGES]
    rows = value["rows"]
    require([(row["case_id"], row["stage"]) for row in rows] == expected,
            "consumer review omits or replaces a planned stage")
    assessed, supported, adverse, unverifiable = 0, 0, 0, 0
    for row, (case_id, stage) in zip(rows, expected):
        case = next(case for case in campaign["cases"] if case["case_id"] == case_id)
        attempt = next((item for item in case["stages"] if item["stage"] == stage), {})
        analysis = attempt.get("analysis") or {}
        message = analysis.get("final_message")
        if attempt.get("model_invoked") and isinstance(message, str):
            relative = attempt["raw_path"] + "/model.stdout.log"
            raw = (root / CONSUMERS / relative).read_bytes()
            require(row["transcript_sha256"] == sha(raw)
                    and row["final_message_sha256"] == sha(message.encode()),
                    "consumer prose review is stale")
            assessment = row["assessment"]
            details = row["unverifiable_details"]
            require(set(assessment) == {"status_accurate", "freshness_accurate", "limitations_accurate"}
                    and all(type(item) is bool for item in assessment.values())
                    and isinstance(details, list)
                    and all(isinstance(item, str) and item.strip() for item in details)
                    and isinstance(row["note"], str) and row["note"].strip(),
                    "explicit consumer interpretation assessment required")
            require(not analysis.get("material_interpretation_failure") or not all(assessment.values()),
                    "internal review cannot erase a machine-detected interpretation failure")
            assessed += 1
            supported += int(all(assessment.values()) and not details)
            adverse += int(not all(assessment.values()))
            unverifiable += int(bool(details))
        else:
            require(row["transcript_sha256"] is None and row["final_message_sha256"] is None
                    and row["assessment"] is None and row["unverifiable_details"] == [] and row["note"],
                    "uncompleted consumer turn cannot receive a successful prose assessment")
    return {"path": REVIEW, "sha256": sha((root / REVIEW).read_bytes()),
            "planned_stages": len(expected), "assessed_messages": assessed,
            "supported_interpretations": supported, "adverse_interpretations": adverse,
            "supported_core_status_freshness_and_limits": assessed - adverse,
            "messages_with_unverifiable_details": unverifiable,
            "unassessed_stages": len(expected) - assessed, "independent_human_review": False,
            "method": value["method"], "rows": rows}


def additive_client_audit(root, campaign):
    """Preserve the frozen analysis and separately reconcile captured failures."""
    from research.softwarex.agent_application_053.consumer_event_audit_v1 import audit
    rows = []
    for case in campaign["cases"]:
        for attempt in case["stages"]:
            if not attempt.get("model_invoked"):
                continue
            raw = (root / CONSUMERS / attempt["raw_path"] / "model.stdout.log").read_bytes()
            result = audit(raw, case["seed"]["root"], case["seed"]["task"], attempt["stage"])
            rows.append({"case_id": case["case_id"], "stage": attempt["stage"], **result})
    helper = root / "research/softwarex/agent_application_053/consumer_event_audit_v1.py"
    return {"helper_sha256": sha(helper.read_bytes()), "rows": rows,
            "additional_fresh_failures": sum(row["additional_fresh_failures"] for row in rows),
            "original_analysis_modified": False, "wire_error_flag_inferred": False,
            "model_retried": False}


def producer_consumer_binding(root):
    """Require the independently opened producer tree to be the consumers' source."""
    from research.softwarex.agent_application_053.prepare_consumers import ORACLE_MANIFEST, ORACLE_COMPLETION
    actual = {name: sha((root / ORACLES / name).read_bytes())
              for name in ("RECORD_MANIFEST.json", "completion.json")}
    require(actual == {"RECORD_MANIFEST.json": ORACLE_MANIFEST, "completion.json": ORACLE_COMPLETION},
            "producer evidence differs from the consumer preparation's pinned source")
    return actual


def build(root=ROOT):
    from research.softwarex.agent_application_053.oracles_v2 import verify as producer_verify
    from research.softwarex.agent_application_053.consumer_continuation_v2 import verify_campaign
    from research.softwarex.sqlglot_supplement_053 import verify as supplement_verify
    root = Path(root)
    binding = producer_consumer_binding(root)
    producer = producer_verify(root / ORACLES)
    consumer = verify_campaign(root / CONSUMERS)
    supplement = supplement_verify(root / SUPPLEMENT)
    require(producer["selected_cases"] == consumer["selected"] == 6
            and producer["completed_verified_fixes"] == consumer["producer_verified_fixes"] == 5
            and consumer["eligible"] == 5, "separate application denominators differ")
    review = review_consumer_messages(root, consumer)
    additive = additive_client_audit(root, consumer)
    return {"schema": "zerorun.actual-agent-application-evidence.0.5.3.v1",
            "records_reconciled": True, "producer": producer, "consumer": consumer,
            "producer_consumer_source_sha256": binding,
            "consumer_interpretation_review": review, "additive_client_event_audit": additive,
            "assisted_sqlglot_supplement": supplement,
            "scope": "Six bounded actual producer attempts; five eligible three-stage installed consumers; one separately assisted repair. Negative primary outcomes remain unchanged. No population reliability, natural demand, independent human replication or journal acceptance probability is inferred."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = build()
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path = ROOT / OUTPUT
    if args.check:
        require(path.read_bytes() == raw, "actual-agent evidence summary is stale")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    print(json.dumps({"records_reconciled": True, "selected_producers": 6,
        "verified_producer_fixes": 5, "eligible_consumers": 5,
        "interpretation_review": {key: value["consumer_interpretation_review"][key]
            for key in ("planned_stages", "assessed_messages", "supported_core_status_freshness_and_limits",
                        "messages_with_unverifiable_details", "supported_interpretations", "adverse_interpretations")},
        "output": OUTPUT, "read_only": args.check}))


if __name__ == "__main__":
    main()
