"""Reconcile the complete frozen campaign and render the journal's numeric text.

This is an arithmetic/identity validator, not an independent scientific review.
Negative outcomes remain data. Missing or inconsistent records fail closed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from research.sqj.run_frozen_campaign import WORKLOADS, IMAGE, LOCK

ROOT = Path(__file__).resolve().parents[2]
ENGINE = "bed70a2d50162fa19d96cef4afec2a19d6c14272b7dd146c850036c0a9743602"
PRODUCER = "96588c65ee674597e4c459651345b9e89ac92cbdc0c85baaa3de4f57f6a44359"
BASE = "21cecc7fc52f55f3e9f1d4e1f3f9f70a65f0d3bb"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    def invalid(value):
        raise ValueError(f"non-finite JSON value: {value}")
    return json.loads(path.read_text(encoding="utf-8-sig"),
                      object_pairs_hook=unique_object, parse_constant=invalid)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numeric(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            f"invalid nonnegative finite measurement: {value!r}")
    return value


def close(actual, expected, label, tolerance=0.06):
    require(type(actual) in (int, float) and math.isfinite(actual), label)
    require(abs(actual - expected) <= tolerance, f"{label}: {actual} != {expected}")


def validate_record(raw, workload, *, expected_base=BASE, expected_engine=ENGINE, expected_producer=PRODUCER):
    name, upstream, frozen, _, targets, _ = workload
    require(raw.get("workload") == name and raw.get("upstream_repo") == upstream,
            "wrong workload identity")
    require(raw.get("frozen_sha") == frozen and raw.get("targets") == list(targets),
            f"{name}: frozen identity/targets changed")
    require(raw.get("methodology_version") == "zerorun.product-generalization-counterbalanced-e2e.v2",
            "wrong methodology")
    require(raw.get("runtime_image") == IMAGE and raw.get("runtime_requirements_sha256") == LOCK,
            "runtime or lock changed")
    require(raw.get("engine_sha") == expected_base, "wrong base commit")
    identity = raw["source_identity"]
    require(identity.get("stable") is True and identity["pre"] == identity["post"],
            "producer source changed during execution")
    require(identity["pre"]["engine_python_source"]["sha256"] == expected_engine and
            identity["pre"]["trial_tool"]["sha256"] == expected_producer, "wrong producer bytes")
    require(raw.get("external_hmac_authority_created") is False and
            raw.get("activation_evidence_kind") == "mechanical-synthetic-formative-fixture",
            "authority evidence boundary changed")
    require(raw.get("case_count") == 20 and len(raw["rows"]) == 20, "incomplete transition denominator")
    require(raw.get("reference_gate") == {"min_compute_efficiency": 5.0,
                                         "min_p95_reduction_percent": 50.0},
            "product threshold altered")
    commits = raw["corpus_commits"]
    require(len(commits) == 21 and len(set(commits)) == 21 and commits[-1] == frozen,
            "invalid frozen commit chain")
    require(len(raw["trajectory_records"]) == 2, "missing trajectory")
    require({t["order"] for t in raw["trajectory_records"]} ==
            {"plain-then-zerorun", "zerorun-then-plain"}, "missing counterbalance")
    ledger = raw["cost_ledger_ms"]
    for value in ledger.values():
        numeric(value)
    direct_fixed = ledger["symmetric_environment_bootstrap_charged_to_each_arm"] + ledger["direct_seed_charged_to_direct"]
    zero_fixed = sum(ledger[k] for k in (
        "symmetric_environment_bootstrap_charged_to_each_arm", "zerorun_seed_charged_to_zerorun",
        "zerorun_qualification_charged_to_zerorun", "zerorun_activation_charged_to_zerorun",
        "automatic_refresh_charged_to_zerorun"))
    observations = []
    for index, row in enumerate(raw["rows"], 1):
        require(row["case"] == index and row["previous_commit"] == commits[index - 1]
                and row["commit"] == commits[index], "missing/reordered transition")
        reps = row["counterbalanced_repetitions"]
        require(len(reps) == 2 and {r["trajectory"] for r in reps} == {1, 2}, "missing/repeated observation")
        require({r["order"] for r in reps} == {"plain-then-zerorun", "zerorun-then-plain"},
                "unbalanced observation")
        for rep in reps:
            require(rep["commit"] == row["commit"] and rep["previous_commit"] == row["previous_commit"],
                    "observation identity mismatch")
            for field in ("reused_nodes", "fresh_nodes", "total_nodes"):
                require(type(rep[field]) is int and rep[field] >= 0, "invalid node accounting")
            require(rep["reused_nodes"] + rep["fresh_nodes"] == rep["total_nodes"], "unreconciled node count")
            comparison = rep["comparison"]
            require(type(comparison["comparison_pass"]) is bool, "missing comparison outcome")
            if comparison["comparison_pass"]:
                require(comparison["exact_node_sequence_match"] and comparison["all_exit_codes_match"]
                        and comparison["independent_shadow_complete_per_node_outcomes"]
                        and comparison["reuse_shadow_valid"], "inconsistent passing comparison")
            numeric(rep["direct_transition_wall_ms"])
            numeric(rep["zerorun_transition_wall_ms"])
            observations.append(rep)
        direct = sum(r["direct_transition_wall_ms"] for r in reps)
        zero = sum(r["zerorun_transition_wall_ms"] for r in reps)
        close(row["direct_ms"], direct + direct_fixed / 20, "direct primary ledger")
        close(row["zerorun_ms"], zero + zero_fixed / 20, "ZeroRun primary ledger")
        for key in ("reused_nodes", "fresh_nodes", "total_nodes"):
            require(row[key] == sum(r[key] for r in reps), "row node total mismatch")
    direct_total = sum(r["direct_ms"] for r in raw["rows"])
    zero_total = sum(r["zerorun_ms"] for r in raw["rows"])
    require(direct_total > 0 and zero_total > 0, "zero campaign cost")
    close(raw["direct_total_ms"], direct_total, "direct total")
    close(raw["zerorun_total_ms"], zero_total, "ZeroRun total")
    speedup = direct_total / zero_total
    direct_p95 = sorted(r["direct_ms"] for r in raw["rows"])[18]
    zero_p95 = sorted(r["zerorun_ms"] for r in raw["rows"])[18]
    p95_reduction = 100 * (1 - zero_p95 / direct_p95)
    close(raw["same_runner_compute_efficiency"], speedup, "speedup", 0.000002)
    close(raw["p95_reduction_percent"], p95_reduction, "p95 reduction", 0.001)
    reused = sum(r["reused_nodes"] for r in observations)
    total = sum(r["total_nodes"] for r in observations)
    require(total > 0 and raw["reused_nodes"] == reused and raw["total_nodes"] == total,
            "campaign node denominator mismatch")
    close(raw["reuse_rate_percent"], 100 * reused / total, "reuse rate", 0.001)
    require(type(raw["safety_pass"]) is bool, "missing safety outcome")
    expected_gate = raw["safety_pass"] and speedup >= 5 and p95_reduction >= 50
    require(raw["performance_gate_pass"] == expected_gate, "product gate changed")
    require(len(raw["adverse_audits"]) == 2, "missing adverse audit")
    direct_steady = sum(r["direct_transition_wall_ms"] for r in observations)
    zero_steady = sum(r["zerorun_transition_wall_ms"] for r in observations)
    return {
        "workload": name, "upstream": upstream, "frozen_sha": frozen, "targets": list(targets),
        "observations": len(observations), "transitions": 20,
        "comparisons_pass": sum(r["comparison"]["comparison_pass"] for r in observations),
        "adverse_checks": len(raw["adverse_audits"]),
        "adverse_checks_pass": sum(a["pass"] is True for a in raw["adverse_audits"]),
        "reviewable_seed_nodes": raw["candidate_reviewable_nodes"],
        "seed_nodes": raw["candidate_node_count"], "reused_nodes": reused, "total_node_observations": total,
        "reuse_percent": 100 * reused / total, "primary_speedup": speedup,
        "steady_speedup": direct_steady / zero_steady, "p95_reduction_percent": p95_reduction,
        "direct_seconds": direct_total / 1000, "zerorun_seconds": zero_total / 1000,
        "direct_fixed_seconds": direct_fixed / 1000, "zerorun_fixed_seconds": zero_fixed / 1000,
        "direct_steady_seconds": direct_steady / 1000, "zerorun_steady_seconds": zero_steady / 1000,
        "safety_checks_pass": raw["safety_pass"], "product_gate_pass": expected_gate,
        "fresh_reason_histogram": raw["candidate_fresh_reason_summary"]["histogram"],
        "stale_successes": raw["stale_successes"], "shadow_mismatches": raw["shadow_mismatches"],
        "direct_failures": raw["direct_failures"], "zerorun_failures": raw["zerorun_failures"],
    }


def analyze(directory):
    campaign = read_json(directory / "campaign-processes.json")
    require(campaign["selected_workloads"] == [w[0] for w in WORKLOADS], "changed campaign selection")
    require([r["workload"] for r in campaign["rows"]] == [w[0] for w in WORKLOADS], "incomplete campaign")
    result = []
    for workload, process in zip(WORKLOADS, campaign["rows"]):
        require(process["status"] == "PROCESS_COMPLETED" and not process.get("benchmark_error"),
                f"{workload[0]}: execution incomplete/error; do not render as completed")
        path = directory / (workload[0] + ".json")
        require(digest(path) == process["raw_receipt_sha256"], "raw receipt changed after execution")
        raw = read_json(path)
        row = validate_record(raw, workload)
        for entry in raw["source_identity"]["pre"]["engine_python_source"]["files"]:
            require(digest(ROOT / "research/sqj/source" / entry["path"]) == entry["sha256"],
                    "archived engine snapshot differs from producer")
        require(digest(ROOT / "research/sqj/source/tools/product_generalization_benchmark.py") == PRODUCER,
                "archived benchmark producer differs")
        require(digest(ROOT / "research/sqj/source/ci/generalization-runtime-requirements.txt") == LOCK,
                "archived runtime lock differs")
        row["raw_receipt_sha256"] = digest(path)
        result.append(row)
    return {"schema": "zerorun.sqj-bounded-analysis.v1", "rows": result,
            "engine_sha256": ENGINE, "producer_sha256": PRODUCER,
            "campaign_record_sha256": digest(directory / "campaign-processes.json"),
            "source_base_commit": BASE, "population_inference": False,
            "agent_effect_measured": False, "external_operator_authority_measured": False,
            "aggregate_primary_speedup": sum(r["direct_seconds"] for r in result) / sum(r["zerorun_seconds"] for r in result),
            "equal_repository_geomean_speedup": math.exp(sum(math.log(r["primary_speedup"]) for r in result) / 5),
            "total_reused_node_observations": sum(r["reused_nodes"] for r in result),
            "total_node_observations": sum(r["total_node_observations"] for r in result)}


def tex(value):
    table = {"&": r"\&", "%": r"\%", "_": r"\_", "#": r"\#", "$": r"\$", "{": r"\{", "}": r"\}"}
    return "".join(table.get(c, c) for c in str(value))


def table(caption, label, columns, header, rows):
    return "\n".join([r"\begin{table}[htbp]", rf"\caption{{{caption}}}\label{{{label}}}",
                      r"\small", r"\begin{tabular}{@{}" + columns + r"@{}}", r"\toprule",
                      header + r" \\", r"\midrule", *[r + r" \\" for r in rows],
                      r"\bottomrule", r"\end{tabular}", r"\end{table}"])


def render(summary, regression):
    rows = summary["rows"]
    passed = sum(r["comparisons_pass"] for r in rows)
    audits = sum(r["adverse_checks_pass"] for r in rows)
    reused = summary["total_reused_node_observations"]
    total = summary["total_node_observations"]
    gate_passes = sum(r["product_gate_pass"] for r in rows)
    low, high = min(r["primary_speedup"] for r in rows), max(r["primary_speedup"] for r in rows)
    abstract = (f"The campaign recorded {passed} agreeing comparisons out of 200 and {audits} passing adverse checks out of ten. "
                f"End-to-end speedup ranged from {low:.3f}x to {high:.3f}x; {reused:,} of {total:,} node observations reused prior successes.")
    workload_rows = [f"{tex(r['workload'])} & \\code{{{r['frozen_sha'][:10]}}} & " +
                     ("Five colorama test modules" if r["workload"] == "colorama" else r"\nolinkurl{" + r["targets"][0] + "}") for r in rows]
    workloads = table("Frozen subjects; full identities and target lists are in the artifact.", "tab:subjects",
                      "llp{0.45\\textwidth}", "Library & Frozen tip & Fixed target", workload_rows)
    performance = table("Measured ratios. Values below 1 indicate slower execution with ZeroRun. The p95 reduction is descriptive.",
                        "tab:performance", "lrrrr", "Library & End-to-end & Steady state & p95 red. (\\%) & Reuse (\\%)",
                        [f"{tex(r['workload'])} & {r['primary_speedup']:.3f} & {r['steady_speedup']:.3f} & {r['p95_reduction_percent']:.2f} & {r['reuse_percent']:.2f}" for r in rows])
    applicability = table("Applicability and observation denominators. Seed eligibility is from the first trajectory; repeated node observations are not independent subjects.",
                          "tab:applicability", "lrrrr", "Library & Seed eligible/total & Reused/observed & Agreement & Adverse",
                          [f"{tex(r['workload'])} & {r['reviewable_seed_nodes']}/{r['seed_nodes']} & {r['reused_nodes']}/{r['total_node_observations']} & {r['comparisons_pass']}/40 & {r['adverse_checks_pass']}/2" for r in rows])
    costs = table("Cost ledger, seconds summed over both trajectories. Fixed costs include environment setup and seeds; ZeroRun additionally includes qualification and activation.",
                  "tab:costs", "lrrrr", "Library & Direct fixed & ZeroRun fixed & Direct total & ZeroRun total",
                  [f"{tex(r['workload'])} & {r['direct_fixed_seconds']:.2f} & {r['zerorun_fixed_seconds']:.2f} & {r['direct_seconds']:.2f} & {r['zerorun_seconds']:.2f}" for r in rows])
    reason_rows = []
    for row in rows:
        histogram = sorted(row["fresh_reason_histogram"], key=lambda x: (-x["count"], x["reason"]))
        if histogram:
            top = histogram[0]
            reason_rows.append(f"\\item {tex(row['workload'])}: {top['count']} seed nodes had the most frequent rejection label, ``{tex(top['reason'])}''. ")
        else:
            reason_rows.append(f"\\item {tex(row['workload'])}: no mandatory-fresh reason was reported at qualification.")
    results = "\n\n".join([
        r"\subsection{RQ1: Fresh agreement and adverse behavior}",
        f"The complete campaign contains 100 transitions and 200 arm comparisons across five libraries. "
        f"The independently captured comparisons agree in {passed}/200 observations; {audits}/10 collection-failure audits pass. "
        f"The receipts record {sum(r['stale_successes'] for r in rows)} stale successes, "
        f"{sum(r['shadow_mismatches'] for r in rows)} shadow mismatches, "
        f"{sum(r['direct_failures'] for r in rows)} normal direct failures, and "
        f"{sum(r['zerorun_failures'] for r in rows)} normal ZeroRun failures. "
        "The injected failures are separate expected nonzero exits, not deleted normal failures. "
        "These are observed counts, not proof of general safety.", applicability,
        r"\subsection{RQ2: Applicability}",
        f"Only {reused:,} of {total:,} measured node observations reused a prior success "
        f"({100 * reused / total:.3f}\\%). Mandatory fresh execution accounts for the remainder. "
        "Table~\\ref{tab:applicability} exposes the denominator: strong fresh agreement may coexist with little useful reuse. "
        "The archived histograms retain all qualification reasons. The largest first-trajectory rejection category for each subject is:",
        r"\begin{itemize}" + "\n" + "\n".join(reason_rows) + "\n" + r"\end{itemize}",
        r"\subsection{RQ3: Complete execution cost}", performance, costs,
        f"The ratio of summed direct to summed ZeroRun campaign costs is {summary['aggregate_primary_speedup']:.3f}x. "
        f"The equal-repository geometric mean is {summary['equal_repository_geomean_speedup']:.3f}x. "
        f"Individual end-to-end ratios range from {low:.3f}x to {high:.3f}x. "
        f"Exactly {gate_passes}/5 subjects meet the unchanged per-workload 5x/50\\% product policy. "
        "The aggregate does not rescue an individual failed product gate. These ratios describe the frozen horizon; "
        "they do not predict a different edit distribution or longer amortization period.",
        r"\subsection{Separate regression and interface evidence}", regression,
        "The repository also retains an older 100-repository non-executing Codex integration receipt: "
        "99 completed integrations, one safe refusal, and zero failures at v0.5.0 commit "
        "\\code{cd97eb5}. "
        "It checked layouts, registration, and protocol behavior, not upstream tests or an autonomous agent episode. "
        "It is not a 100-repository runtime study and is not evidence for the new overlay's performance. "
        "The paper's runtime subject count remains five."])
    conclusion = (f"Across five libraries and 100 transitions, {passed}/200 fresh comparisons and {audits}/10 adverse checks passed, "
                  f"while the ratio of complete summed costs was {summary['aggregate_primary_speedup']:.3f}x and "
                  f"{gate_passes}/5 workloads met the unchanged product performance policy.")
    return {"ABSTRACT_RESULTS": abstract, "WORKLOAD_TABLE": workloads, "RESULTS": results, "CONCLUSION_RESULTS": conclusion}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=ROOT / "research/sqj/evidence/campaign-1")
    parser.add_argument("--output", type=Path, default=ROOT / "research/sqj/generated")
    parser.add_argument("--windows-run", type=Path, default=ROOT / "docs/evidence/softwarex-regression/windows-20260906-sqj-2")
    parser.add_argument("--linux-run", type=Path, default=ROOT / "research/sqj/evidence/linux-regression-1")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    summary = analyze(args.campaign)
    receipt = read_json(args.windows_run / "receipt.json")
    require(receipt["source_unchanged"] is True, "Windows source changed mid-run")
    require(receipt["pytest_exit_code"] == 0, "Windows regression did not pass")
    log = (args.windows_run / "pytest.log").read_text(encoding="utf-8-sig", errors="replace")
    final_lines = [line for line in log.splitlines() if " passed" in line and " in " in line and re.search(r"\d+(?:\.\d+)?s", line)]
    require(final_lines, "missing final pytest result")
    final_line = re.sub(r"=+", "", final_lines[-1]).strip()
    regression = ("The fresh Windows host regression reported ``" + tex(final_line) +
                  "''. Its source snapshot was unchanged during the run. This is direct host validation on CPython 3.14.6, "
                  "not the hash-locked Linux runtime qualification. Windows-inapplicable or privilege-dependent cases remain skipped, not passed. "
                  "An earlier run's two research-fixture failures were preserved; the fixture was corrected to copy declared artifact inputs "
                  "rather than unrelated scratch directories, retaining baseline and tamper assertions.")
    summary["windows_receipt_sha256"] = digest(args.windows_run / "receipt.json")
    summary["windows_pytest_summary"] = final_line
    summary["windows_exit_code"] = receipt["pytest_exit_code"]
    required_tests = (
        "test_node_cache_miss_publish_hit_and_source_edit_invalidation",
        "test_verified_action_snapshot_rechecks_source_before_return",
        "test_reordered_shared_nodes_fail_closed_to_whole_target_fresh",
        "test_dynamic_builtins_import_remains_fresh_required",
        "test_monitoring_detects_transient_py_resume_callback_replacement",
    )
    junit = ET.parse(args.windows_run / "junit.xml")
    for name in required_tests:
        matches = [t for t in junit.iter("testcase") if t.get("name") == name]
        require(len(matches) == 1 and not any(matches[0].find(tag) is not None for tag in
                ("failure", "error", "skipped")), "traceability case did not pass: " + name)
    summary["windows_traceability_test_ids"] = list(required_tests)
    linux = read_json(args.linux_run / "receipt.json")
    require(linux["source_unchanged"] is True and linux["pytest_exit_code"] == 0
            and linux["timed_out"] is False, "Linux regression incomplete or failed")
    linux_log = (args.linux_run / "pytest.log").read_text(encoding="utf-8", errors="replace")
    linux_lines = [line for line in linux_log.splitlines() if " passed" in line and " in " in line and re.search(r"\d+(?:\.\d+)?s", line)]
    require(linux_lines, "missing Linux pytest result")
    linux_final = re.sub(r"=+", "", linux_lines[-1]).strip()
    summary["linux_receipt_sha256"] = digest(args.linux_run / "receipt.json")
    summary["linux_pytest_summary"] = linux_final
    regression += (" The separate fresh Linux host run reported ``" + tex(linux_final) +
                   "'' on CPython 3.10.12, with unchanged source identity. Neither host regression is a model-driven agent evaluation.")
    historical_path = ROOT / "research/results/formative/cd97eb5/raw/commercial-100-repository-result.json"
    historical = read_json(historical_path)
    require(historical["engine_commit"] == "cd97eb55727a41e20354c9a8c9747c28dae525a6" and
            historical["engine_version"] == "0.5.0" and historical["attempted_repositories"] == 100 and
            historical["distinct_repositories"] == 100 and len(historical["rows"]) == 100 and
            historical["counts"] == {"pass": 99, "safe_refusal": 1, "failure": 0}, "historical integration identity/counts changed")
    summary["historical_integration_sha256"] = digest(historical_path)
    document = (ROOT / "research/sqj/paper/main.tex.in").read_text(encoding="utf-8")
    for key, value in render(summary, regression).items():
        require(document.count("@@" + key + "@@") == 1, "missing/repeated manuscript slot")
        document = document.replace("@@" + key + "@@", value)
    require("@@" not in document, "unresolved manuscript slot")
    abstract = re.search(r"\\abstract\{(.*?)\}\s*\\keywords", document, re.S).group(1)
    require(150 <= len(abstract.split()) <= 250, "journal abstract word limit")
    csv_buffer = io.StringIO(newline="")
    fields = [k for k in summary["rows"][0] if k not in {"targets", "fresh_reason_histogram"}]
    writer = csv.DictWriter(csv_buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(summary["rows"])
    outputs = {
        args.output / "summary.json": json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        args.output / "results.csv": csv_buffer.getvalue(),
        ROOT / "research/sqj/paper/main.tex": document,
    }
    for path, content in outputs.items():
        if args.check:
            require(path.read_text(encoding="utf-8") == content, f"generated output drift: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "PASS", "meaning": "identity/arithmetic/table checks only",
                      "subjects": len(summary["rows"]), "windows_exit_code": receipt["pytest_exit_code"],
                      "product_gate_passes": sum(r["product_gate_pass"] for r in summary["rows"])}))


if __name__ == "__main__":
    main()
