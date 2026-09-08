"""Build a source-indexed consumer interpretation and Lizard fixture audit.

Only saved records are read. No model, upstream code, Docker command or new test
workload is executed. Literal model claims are compared with recorded fields;
this does not mechanically certify all prose or supply human author approval.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import io
import json
from pathlib import Path
import re
import tarfile

from research.softwarex import build_agent_application_evidence as app
from research.softwarex import verify_recovered_handoff_v6 as recovered_reader
from research.softwarex.agent_application_053 import validation as v

ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = "research/softwarex/generated/consumer-lizard-audit-v1.json"
MARKDOWN_PATH = "research/softwarex/CONSUMER_LIZARD_AUDIT.md"
WIRE = re.compile(r"\bisError\b\s*(?:(?:=|:)\s*|was\s+)?(true|false)\b")
STATUS = {"available": ("HIT_REUSED", 0, False, "success"),
          "fresh": ("VERIFY_MATCH", 0, True, "success"),
          "restored": ("MISS_FAILED", 1, True, "failure")}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(root, relative):
    return v.strict(v.real(Path(root) / relative).read_bytes())


def binding(root, relative):
    raw = v.real(Path(root) / relative).read_bytes()
    return {"path": relative, "bytes": len(raw), "sha256": v.sha(raw)}


def capture_counts(value):
    """Count actual call outcomes without replacing failure/skip denominators."""
    nodes, outcomes = value["nodeids"], value["outcomes"]
    require(isinstance(nodes, list) and nodes and len(nodes) == len(set(nodes))
            and len(nodes) == len(outcomes), "capture has missing/duplicate outcomes")
    require(all(isinstance(n, str) and n for n in nodes), "invalid node identity")
    require(type(value["exit_code"]) is int, "capture exit code is not an integer")
    counts = Counter(row.get("call", "not_called") for row in outcomes)
    require(set(counts) <= {"passed", "failed", "skipped", "not_called"},
            "unrecognized call disposition")
    return {"collected": len(nodes), "passed": counts["passed"],
            "failed": counts["failed"], "skipped": counts["skipped"],
            "not_called": counts["not_called"], "exit_code": value["exit_code"],
            "xfail_marked": sum(bool(row.get("wasxfail")) for row in outcomes),
            "failed_nodeids": [node for node, row in zip(nodes, outcomes)
                               if row.get("call") == "failed"]}


def literal_consumer_row(raw, case_id, stage, root_identity, task):
    """Extract exact raw-event locators and literal, independently visible claims."""
    events = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        require(line.strip(), "blank/non-JSON transcript line")
        events.append((line_number, v.strict(line.encode("utf-8"))))
    calls = [(n, event["item"]) for n, event in events
             if event.get("type") == "item.completed"
             and event.get("item", {}).get("type") == "mcp_tool_call"]
    require(len(calls) == 1, "one actual completed MCP call required")
    n, call = calls[0]
    require(call.get("server") == "zerorun" and call.get("tool") == "run_tests"
            and call.get("error") is None, "unexpected tool/transport error")
    arguments = call["arguments"]
    require(arguments.get("root") == root_identity and arguments.get("task") == task,
            "recorded root/task differs")
    result = call["result"]
    require(set(result) == {"content", "structured_content"},
            "client wire-field capture shape changed")
    payload = result["structured_content"]
    require(len(result["content"]) == 1 and result["content"][0]["type"] == "text"
            and v.strict(result["content"][0]["text"].encode()) == payload,
            "text and structured result disagree")
    status, code, executed, outcome = STATUS[stage]
    require(payload["status"] == status and type(payload["exit_code"]) is int
            and payload["exit_code"] == code and payload["task"] == task,
            "recorded stage result differs")
    require(call["status"] == ("failed" if stage == "restored" else "completed"),
            "unexpected recorded client item status")
    messages = [(i, e["item"]) for i, e in events
                if e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message" and i > n]
    require(len(messages) == 1 and events[-1][1].get("type") == "turn.completed",
            "one final message and completed model turn required")
    message_line, message_item = messages[0]
    message = message_item["text"]
    claim = v.strict(message.encode("utf-8"))
    require(type(claim.get("execution_in_this_call")) is bool
            and claim.get("validation_status") in {"success", "failure", "unknown"}
            and isinstance(claim.get("diagnostics"), str)
            and isinstance(claim.get("limitations"), str), "literal interpretation fields absent")
    matches = [{"excerpt": match.group(), "claimed_value": match[1] == "true",
                "decoded_final_message_char_start": match.start(),
                "decoded_final_message_char_end": match.end(),
                "support": "UNVERIFIABLE_WIRE_FIELD_NOT_CAPTURED"}
               for match in WIRE.finditer(message)]
    require(len(matches) == message.count("isError"), "unindexed wire assertion spelling")
    return {"case_id": case_id, "stage": stage,
            "transcript_sha256": v.sha(raw), "final_message_sha256": v.sha(message.encode()),
            "tool_result_locator": {"jsonl_line": n, "json_pointer": "/item/result/structured_content"},
            "final_message_locator": {"jsonl_line": message_line, "json_pointer": "/item/text",
                                      "item_id": message_item["id"], "decode_text_as_json": True},
            "observed_status": status, "observed_exit_code": code,
            "client_item_status": call["status"], "wire_error_flag_captured": False,
            "model_execution_in_this_call": claim["execution_in_this_call"],
            "model_validation_status": claim["validation_status"],
            "freshness_claim_matches_recorded_status": claim["execution_in_this_call"] == executed,
            "outcome_claim_matches_recorded_status": claim["validation_status"] == outcome,
            "literal_claim_locators": {"freshness": "/execution_in_this_call", "outcome": "/validation_status"},
            "diagnostics_excerpt": claim["diagnostics"].split(". ", 1)[0].rstrip(".") + ".",
            "diagnostics_full": claim["diagnostics"], "limitations_excerpt": claim["limitations"],
            "extra_wire_assertions": matches,
            "other_prose_claims_mechanically_certified": False,
            "independent_human_or_author_review_claimed": False}


def consumer_rows(root, application):
    review = {(r["case_id"], r["stage"]): r
              for r in application["consumer_interpretation_review"]["rows"]}
    rows = []
    for case in application["consumer"]["cases"]:
        require([a["stage"] for a in case["stages"]] == list(STATUS), "stage denominator/order differs")
        for attempt in case["stages"]:
            require(attempt["model_invoked"] and attempt["records_reconciled"]
                    and attempt["process_completed_successfully"], "unfinished model stage")
            relative = app.CONSUMERS + "/" + attempt["raw_path"] + "/model.stdout.log"
            raw = v.real(Path(root) / relative).read_bytes()
            row = literal_consumer_row(raw, case["case_id"], attempt["stage"],
                                       case["seed"]["root"], case["seed"]["task"])
            old = review[(case["case_id"], attempt["stage"])]
            require(row["transcript_sha256"] == old["transcript_sha256"]
                    and row["final_message_sha256"] == old["final_message_sha256"]
                    and row["final_message_sha256"] == v.sha(attempt["analysis"]["final_message"].encode()),
                    "raw final message differs from reconciled interpretation record")
            require(bool(row["extra_wire_assertions"]) == bool(old["unverifiable_details"]),
                    "literal wire claims differ from retained review")
            rows.append({"response": binding(root, relative), **row})
    require(len(rows) == 15 and len({r["case_id"] for r in rows}) == 5,
            "actual consumer denominator changed")
    require(sum(bool(r["extra_wire_assertions"]) for r in rows) == 13,
            "wire-assertion denominator changed")
    return rows


def derive_case_rows(root, application):
    """Reuse an already strictly reconciled application without rerunning readers."""
    eligible = {c["case_id"] for c in application["consumer"]["cases"]}
    rows = []
    for case in application["producer"]["cases"]:
        cid = case["case_id"]
        paths = {state: app.ORACLES + "/cases/" + cid + "/" + state + "-capture/raw-outcomes.json"
                 for state in ("baseline", "final")}
        captures = {state: read(root, path) for state, path in paths.items()}
        require(captures["baseline"]["nodeids"] == captures["final"]["nodeids"],
                "primary final collected nodes differ")
        counts = {state: capture_counts(value) for state, value in captures.items()}
        classification = case["classification"]
        require(counts["final"]["exit_code"] == classification["final_exit_code"]
                and counts["baseline"]["exit_code"] == classification["baseline_exit_code"],
                "primary oracle classification differs")
        rows.append({"case_id": cid, "target_nodes": counts["final"]["collected"],
                     "baseline": counts["baseline"], "final": counts["final"],
                     "classification": classification["classification"],
                     "primary_consumer_eligible": cid in eligible,
                     "model_consumer_stages": 3 if cid in eligible else 0,
                     "raw_captures": {state: binding(root, path) for state, path in paths.items()},
                     "assisted_repair_changes_primary_outcome": False})
    require(len(rows) == 6 and sum(row["primary_consumer_eligible"] for row in rows) == 5,
            "primary six-case denominator changed")
    return rows


def lizard_row(root, recovered):
    root = Path(root)
    r5, r6 = recovered[5], recovered[6]
    prefix = recovered_reader.PREFIX + "v6/"
    correction = r6["fixture_correction"]
    require(correction["exact_single_assertion_change_verified"]
            and correction["case_ids_order_targets_base_archives_and_runtime_patches_unchanged"],
            "fixture scope not independently reconciled")
    oldbase = root / recovered_reader.ORIGINAL
    newbase = root / (prefix + "corrected-acquisition")
    old = read(root, recovered_reader.ORIGINAL + "/main.json")
    item = next(c for c in old["cases"] if c["case_id"] == recovered_reader.LIZARD)
    prior = read(oldbase, item["metadata"]["path"])["rows"][0]["row"]
    new = read(newbase, "cases/" + recovered_reader.LIZARD + "/metadata.json")["rows"][0]["row"]
    archive = v.real(oldbase / item["source_archive"]["path"]).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.endswith("/" + recovered_reader.TARGET)]
        require(len(members) == 1 and members[0].isfile(), "ambiguous original fixture archive")
        original_source = tar.extractfile(members[0]).read().decode("utf-8")
    before = recovered_reader.apply_target_patch(original_source, prior["test_patch"])
    after = recovered_reader.apply_target_patch(original_source, new["test_patch"])
    def method(text):
        tree = ast.parse(text)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Test_Big")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "test_typedef")
        return {"start_line": fn.lineno, "end_line": fn.end_lineno,
                "text": "\n".join(text.splitlines()[fn.lineno - 1:fn.end_lineno]),
                "source_sha256": v.sha(text.encode())}
    paths = {"patch": prefix + "corrected-acquisition/lizard191-fixture.patch",
             "amendment": prefix + "record-only/provenance/APPLICATION_REVISION_V6_AMENDMENT.md",
             "correction": prefix + "corrected-acquisition/FIXTURE_CORRECTION.json",
             "upstream": prefix + "upstream-fixture-reference/testCAndCPP.py",
             "retrieval": prefix + "upstream-fixture-reference/retrieval.json"}
    upstream = v.real(root / paths["upstream"]).read_text(encoding="utf-8")
    observations = []
    for version in (5, 6):
        base = recovered_reader.PREFIX + "v" + str(version) + "/record-only/run/cases/" + recovered_reader.LIZARD
        captures = [("compatibility", base + "/compatibility-capture/raw-outcomes.json")]
        if version == 6:
            captures += [(f"block-{block}-{arm}-fresh-oracle", base + f"/block-{block}/{arm}/oracle-capture/raw-outcomes.json")
                         for block in (0, 1) for arm in ("fresh", "zerorun")]
        for label, path in captures:
            observations.append({"cohort": "V" + str(version), "scope": label,
                                 "capture": binding(root, path), **capture_counts(read(root, path))})
    confirmed = prefix + "launch-evidence/preflight-records/lizard-first/raw-outcomes.json"
    observations.append({"cohort": "V6", "scope": "separate preflight combined target",
                         "capture": binding(root, confirmed), **capture_counts(read(root, confirmed))})
    return {"case_id": recovered_reader.LIZARD, "target": recovered_reader.TARGET,
            "before_expected": 2, "after_expected": 3,
            "before_method": method(before), "after_method": method(after),
            "upstream_method": method(upstream),
            "exact_patch": v.real(root / paths["patch"]).read_text(encoding="utf-8"),
            "files": {k: binding(root, path) for k, path in paths.items()},
            "original_source_archive": binding(root, recovered_reader.ORIGINAL + "/" + item["source_archive"]["path"]),
            "original_metadata": binding(root, recovered_reader.ORIGINAL + "/" + item["metadata"]["path"]),
            "upstream": correction["upstream_reference"], "strict_correction_check": correction,
            "cohorts": [{"cohort": "V" + str(n),
                         "selected": recovered[n]["controlled_handoffs"]["selected_cases"],
                         "completed": recovered[n]["controlled_handoffs"]["complete_cases"],
                         "completion": binding(root, recovered_reader.PREFIX + "v" + str(n) + "/record-only/run/completion.json")}
                        for n in (5, 6)],
            "fresh_observations": observations,
            "scope": "V5 remains 23/24 with the original failure. V6 is a separately labeled corrected-fixture 24/24 repeat, not an unmodified replay or a new algorithm. The pinned upstream method was retrieved during the recovery audit; missing original diagnostic bytes are not claimed recovered. Missing fine-grained host monitoring still qualifies V6 timing."}


def build(root=ROOT, *, application=None, recovered=None):
    root = Path(root)
    if application is None:
        application = app.build(root)
        require(application == read(root, app.OUTPUT), "saved application summary is stale")
    if recovered is None:
        recovered = {n: recovered_reader.verify(root / (recovered_reader.PREFIX + "v" + str(n)) / "record-only",
                                               root / recovered_reader.ORIGINAL, version=n) for n in (5, 6)}
    rows = consumer_rows(root, application)
    return {"schema": "zerorun.consumer-lizard-audit.v1",
            "scope": "Source-indexed observations from existing sealed records, with literal field comparison and exact excerpts. No human author review, independent human study, new model call, experiment, or acceptance probability is claimed. Other prose assertions remain subject to review.",
            "consumer_rows": rows, "selected_case_rows": derive_case_rows(root, application),
            "lizard_correction": lizard_row(root, recovered),
            "summary": {"consumer_rows": len(rows), "eligible_cases": 5, "selected_producers": 6,
                        "literal_freshness_matches": sum(r["freshness_claim_matches_recorded_status"] for r in rows),
                        "literal_outcome_matches": sum(r["outcome_claim_matches_recorded_status"] for r in rows),
                        "messages_with_unverifiable_wire_assertions": sum(bool(r["extra_wire_assertions"]) for r in rows)},
            "source_readers": [binding(root, "research/softwarex/build_agent_application_evidence.py"),
                               binding(root, "research/softwarex/verify_recovered_handoff_v6.py")],
            "retained_internal_review": binding(root, app.REVIEW)}


def markdown(value):
    def link(row, label="record", line=None):
        path = row["path"].removeprefix("research/softwarex/")
        return f"[{label}]({path}" + (f"#L{line}" if line else "") + ")"
    def safe(text):
        return text.replace("|", "\\|").replace("\n", " ")
    out = ["# Consumer responses and Lizard fixture correction", "", value["scope"], "",
           "Run `python -B -m research.softwarex.build_consumer_lizard_audit --check` from the complete source checkout to reconcile the saved records and compare both this Markdown and the machine-readable [audit](generated/consumer-lizard-audit-v1.json). This does not invoke models, Docker or upstream tests.", "",
           "## Six original selected issues", "",
           "These are actual model-produced final patches with unchanged supplied targets. The separate assisted SQLGlot repair passes 99 nodes but changes neither its original primary outcome nor consumer eligibility.", "",
           "| Selected issue | Baseline passed / failed | Original final passed / failed | Consumer stages | Fresh source |",
           "|---|---:|---:|---:|---|"]
    for row in value["selected_case_rows"]:
        out.append(f"| `{row['case_id']}` | {row['baseline']['passed']} / {row['baseline']['failed']} | {row['final']['passed']} / {row['final']['failed']} | {row['model_consumer_stages']} | {link(row['raw_captures']['baseline'], 'baseline')} / {link(row['raw_captures']['final'], 'final')} |")
    out += ["", "## All fifteen actual consumer responses", "",
            "Each source link identifies the physical JSONL line containing the final agent message (`/item/text`). Decode that text as JSON: `/execution_in_this_call` is the literal historical-versus-fresh claim and `/validation_status` is the literal outcome claim. The observed result is separately at `/item/result/structured_content`; its precise line, full excerpts, response hash and decoded-message hash are in the JSON audit. `false` means no execution in this call; `true` means newly executed validation.", "",
            "The table compares those two explicit claims with recorded status and exit code. It does not certify every detail of prose. All fifteen exports omit the wire `isError` field. Thirteen messages additionally assert it; those assertions are **unverifiable from these records, not established false**. Client `status=failed` with an intact `MISS_FAILED` payload is separately observed for the five restored states. Restoration was operator-controlled, not an autonomous model edit. No human-author review is asserted.", "",
            "| Case / stage | Recorded status; exit | Model executes now; outcome | Exact diagnostics excerpt | Extra wire claim | Response locator |",
            "|---|---|---|---|---|---|"]
    for row in value["consumer_rows"]:
        wire = "; ".join('`' + m['excerpt'] + '` — unverifiable' for m in row['extra_wire_assertions']) or "Not asserted"
        out.append(f"| `{row['case_id']}` / {row['stage']} | `{row['observed_status']}`; {row['observed_exit_code']} | `{str(row['model_execution_in_this_call']).lower()}`; `{row['model_validation_status']}` | {safe(row['diagnostics_excerpt'])} | {wire} | {link(row['response'], 'JSONL line ' + str(row['final_message_locator']['jsonl_line']), row['final_message_locator']['jsonl_line'])} |")
    lizard = value["lizard_correction"]
    out += ["", "## Indexed Lizard correction", "", lizard["scope"], "",
            "Only `Test_Big.test_typedef` in `test/test_languages/testCAndCPP.py` changes the expected cyclomatic complexity from 2 to 3. The existing strict reader reconstructs both targets from the same original archive and test patches, checks every other byte, and confirms unchanged case order, targets, base archives and production/reference patches. The corrected method's AST matches the pinned public upstream method. This is an explicit intervention, not an original-cohort pass.", "",
            f"Records: {link(lizard['files']['patch'], 'exact diff')}, {link(lizard['files']['correction'], 'correction record')}, {link(lizard['files']['amendment'], 'prospective V6 amendment')}, {link(lizard['files']['retrieval'], 'upstream retrieval')}. Upstream: [{lizard['upstream']['commit']}]({lizard['upstream']['url']}), method lines {lizard['upstream_method']['start_line']}–{lizard['upstream_method']['end_line']}; retained source SHA-256 `{lizard['upstream']['file']['sha256']}`.", "", "```diff", lizard["exact_patch"].rstrip(), "```", "",
            "| Cohort | Completed / selected | Unmodified outcome ledger |", "|---|---:|---|"]
    for row in lizard["cohorts"]:
        out.append(f"| {row['cohort']} | {row['completed']} / {row['selected']} | {link(row['completion'])} |")
    out += ["", "| Retained fresh check | Collected | Passed | Failed | Exit | Raw per-node evidence |",
            "|---|---:|---:|---:|---:|---|"]
    for row in lizard["fresh_observations"]:
        out.append(f"| {row['cohort']} {row['scope']} | {row['collected']} | {row['passed']} | {row['failed']} | {row['exit_code']} | {link(row['capture'])} |")
    out += ["", "The separate combined-target preflight has its own denominator; it is not an extra paired block. These are saved fresh-check observations, not tests executed by this audit. The before/after method text, reconstructed source hashes, archive/metadata bindings, actual failed node and every linked file's byte count/hash are in the JSON audit.", ""]
    return "\n".join(out)


def check_outputs(root, value):
    expected_json = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    expected_md = markdown(value).encode()
    require(v.real(Path(root) / JSON_PATH).read_bytes() == expected_json, "JSON audit is absent or stale")
    require(v.real(Path(root) / MARKDOWN_PATH).read_bytes() == expected_md, "Markdown audit is absent or stale")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = build()
    if args.check:
        check_outputs(ROOT, value)
    else:
        (ROOT / JSON_PATH).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        (ROOT / MARKDOWN_PATH).write_text(markdown(value), encoding="utf-8", newline="\n")
    print(json.dumps({"passed": True, "read_only": args.check, **value["summary"]}))


if __name__ == "__main__":
    main()
