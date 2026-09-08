"""Additive audit of client-recorded fresh failures; never rewrite frozen analysis.

The pinned CLI can represent a tool-level test failure as item status "failed"
with an intact result and error=null while omitting wire isError metadata. This
helper classifies only that exact recorded representation. Run it after the
frozen attempt validator has bound source, command, client and raw stream.
"""
from __future__ import annotations
import argparse
from pathlib import Path

if __package__:
    from . import validation as v
else:
    import validation as v

SCHEMA = "zerorun.real-consumer-additive-client-failure-audit.0.5.3.v1"


def audit(raw, root, task, stage):
    """Return additive observations only; reject malformed/contradictory inputs.

    A successful audit means recorded evidence reconciles, not that tests passed
    or that a model interpretation passed. Wire isError was not captured.
    """
    v.require(isinstance(raw, bytes), "raw client bytes required")
    original = v.analyze_events(raw, root=root, task=task, stage=stage)
    v.require(original["boundary_pass"] and original["model_turn_completed"],
              "frozen boundary/complete-turn check must pass before additive audit")
    events = [v.strict(line) for line in raw.splitlines()]
    additional = []
    for index, event in enumerate(events):
        item = event.get("item", {})
        if (event["type"] != "item.completed" or item.get("type") != "mcp_tool_call"
                or item.get("tool") != "run_tests" or item.get("status") != "failed"):
            continue
        v.require("error" in item and item["error"] is None,
                  "failed client call has an execution/transport error")
        args = item["arguments"]
        v.require(args.get("root") == root and args.get("task") == task
                  and set(args) <= {"root", "task", "verify"}
                  and type(args.get("verify", False)) is bool,
                  "exact qualified root/task/options required")
        result = item.get("result")
        v.require(isinstance(result, dict) and set(result) <= {"content", "structured_content", "structuredContent"}
                  and not ({"is_error", "isError"} & set(result)),
                  "audit applies only to the captured result without a wire error flag")
        payload, _unused_default_error = v.payload_from_call(item)
        # Do not use payload_from_call's default false value as observed wire data.
        v.require(payload.get("status") == "MISS_FAILED" and payload.get("task") == task
                  and type(payload.get("exit_code")) is int and payload["exit_code"] > 0
                  and payload.get("verified") is False and payload.get("restored_outputs") == []
                  and payload.get("mode") == "reuse" and v.HEX.fullmatch(str(payload.get("cache_key"))),
                  "failed client result is not an exact fresh test failure")
        v.require("root" not in payload or payload["root"] == root, "result root differs")
        for name in ("stdout_tail", "stderr_tail"):
            v.require(isinstance(payload.get(name), str) and len(payload[name]) <= 4000,
                      "fresh diagnostic tail exceeds captured bound")
        additional.append({"call_id": item["id"], "event_index": index,
            "classification": "FRESH_FAILURE", "arguments": args, "payload": payload,
            "provenance": "intact client-recorded tool result, not a reconstructed wire response",
            "client_item_status": "failed", "client_error": None,
            "wire_error_flag_captured": False, "wire_is_error": None})
    return {"schema": SCHEMA, "passed": True, "raw_sha256": v.sha(raw), "raw_bytes": len(raw),
            "root": root, "task": task, "stage": stage,
            "original_analysis_sha256": v.sha(v.canonical(original)),
            "original_boundary_pass": original["boundary_pass"],
            "original_completed_result_count": len(original["completed_mcp_results"]),
            "original_errors": original["errors"], "original_analysis_modified": False,
            "additional_results": additional, "additional_fresh_failures": len(additional),
            "fresh_request_satisfied_additionally": any(row["arguments"].get("verify") is True for row in additional),
            "reuse_observed_additionally": False, "wire_error_flag_inferred": False,
            "natural_language_review_required": True, "model_retried": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw", required=True, type=Path)
    p.add_argument("--root", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--stage", required=True, choices=v.STAGES)
    args = p.parse_args(argv)
    path = v.real(args.raw)
    v.require(path.stat().st_size <= v.LIMIT, "raw stream exceeds bound")
    print(v.canonical(audit(path.read_bytes(), args.root, args.task, args.stage)).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
