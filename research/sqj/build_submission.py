"""Generate the manuscript only from reconciled final experimental evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import statistics
import xml.etree.ElementTree as ET

from research.sqj.analyze_campaign import read_json, require, digest, tex, table, numeric
from research.sqj.analyze_comparison import analyze, historical_partial, numerical_tables
from research.sqj.run_frozen_campaign import WORKLOADS
from research.sqj.analyze_current_revision import analyze as current_revision

ROOT = Path(__file__).resolve().parents[2]
SQJ = ROOT / "research/sqj"
MAIN = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
PACKAGE_DIRECTORY = ROOT / "output/packages/zerorun-sqj-delivery-20260906"
PACKAGE_RECEIPT = SQJ / "evidence/package-validation-delivery.json"
TRACEABILITY_CASES = (
    "test_node_cache_miss_publish_hit_and_source_edit_invalidation",
    "test_verified_action_snapshot_rechecks_source_before_return",
    "test_reordered_shared_nodes_fail_closed_to_whole_target_fresh",
    "test_dynamic_builtins_import_remains_fresh_required",
    "test_monitoring_detects_transient_py_resume_callback_replacement",
    "test_readonly_whole_task_hit_does_not_stage_or_execute",
    "test_readonly_hit_rechecks_source_after_authenticated_lookup",
    "test_force_and_verify_bypass_readonly_hit_optimization",
)


def pytest_summary(path):
    log = path.read_text(encoding="utf-8-sig", errors="strict")
    lines = [line for line in log.splitlines() if re.search(r"\b\d+ passed", line) and re.search(r"\bin \d+(?:\.\d+)?s", line)]
    require(len(lines) == 1, "missing or ambiguous terminal pytest summary")
    line = lines[0]
    require(not re.search(r"\b\d+ (?:failed|errors?)\b", line), "regression failure in terminal summary")
    counts = {}
    for field in ("passed", "skipped", "subtests passed"):
        matches = re.findall(r"\b(\d+) " + field + r"\b", line)
        counts[field] = int(matches[0]) if matches else 0
    counts["seconds"] = float(re.search(r"\bin (\d+(?:\.\d+)?)s", line).group(1))
    return counts


def host_regression(directory):
    receipt = read_json(directory / "receipt.json")
    require(receipt["pytest_exit_code"] == 0 and receipt["source_unchanged"] is True, "host regression failed or source drifted")
    require(receipt["source_before_sha256"] == receipt["source_after_sha256"], "host source identities disagree")
    require(receipt.get("timed_out", False) is False, "host regression timed out")
    summary = pytest_summary(directory / "pytest.log")
    junit = ET.parse(directory / "junit.xml")
    cases = list(junit.iter("testcase"))
    require(all(t.find("failure") is None and t.find("error") is None for t in cases), "JUnit contains failed cases")
    skipped = sum(t.find("skipped") is not None for t in cases)
    require(skipped == summary["skipped"], "JUnit and terminal skip counts differ")
    require(len(cases) == summary["passed"] + summary["skipped"], "JUnit primary-case/terminal denominator mismatch")
    suites = list(junit.iter("testsuite"))
    require(len(suites) == 1 and int(suites[0].get("tests")) == len(cases) + summary["subtests passed"], "JUnit aggregate/subtest denominator mismatch")
    return {**summary, "receipt_sha256": digest(directory / "receipt.json"), "junit_sha256": digest(directory / "junit.xml"), "source_sha256": receipt["source_before_sha256"]}


def validate_binding(binding, experiment, git_archive):
    require(binding["all_files_bound"] is True and binding["main_commit"] == MAIN, "source not bound to tested main")
    names = {p.relative_to(experiment).as_posix() for p in experiment.rglob("*") if p.is_file()}
    rows = binding["files"]
    require(len(rows) == len(names) and {r["path"] for r in rows} == names, "incomplete or duplicate source binding")
    for row in rows:
        original = experiment / row["path"]
        committed = git_archive / row["path"]
        require(digest(original) == row["experiment_sha256"], "source binding archive drift")
        require(digest(committed) == row["commit_blob_sha256"], "source binding Git archive drift")
        original_bytes, committed_bytes = original.read_bytes(), committed.read_bytes()
        equal = original_bytes == committed_bytes
        require(row["byte_identical"] is equal and row["only_physical_newline_difference"] is (not equal), "source binding difference mislabeled")
        require(original_bytes.replace(b"\r\n", b"\n") == committed_bytes.replace(b"\r\n", b"\n"), "non-newline source binding difference")


def engineering_evidence():
    windows = host_regression(ROOT / "docs/evidence/softwarex-regression/windows-20260906-readonly-hit-final")
    junit = ET.parse(ROOT / "docs/evidence/softwarex-regression/windows-20260906-readonly-hit-final/junit.xml")
    for name in TRACEABILITY_CASES:
        cases = [t for t in junit.iter("testcase") if t.get("name") == name]
        require(len(cases) == 1 and all(cases[0].find(tag) is None for tag in ("failure", "error", "skipped")), "named implementation check did not pass: " + name)
    github = SQJ / "evidence/github-86f4228"
    jobs = read_json(github / "jobs.json")["jobs"]
    matrix = {}
    for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        name = "regression-python-" + version
        matched = [j for j in jobs if j["name"] == name]
        require(len(matched) == 1 and matched[0]["head_sha"] == MAIN and matched[0]["conclusion"] == "success", "CI regression not successful on exact main")
        path = github / (name + ".log")
        matrix[version] = {**pytest_summary(path), "log_sha256": digest(path), "job_url": matched[0]["html_url"]}
    integration_path = github / "commercial-100/commercial-100-repository-result.json"
    integration = read_json(integration_path)
    require(integration["engine_commit"] == MAIN and integration["engine_version"] == "0.5.1", "integration tested a different engine")
    require(integration["attempted_repositories"] == integration["distinct_repositories"] == len(integration["rows"]) == 100, "integration denominator mismatch")
    require(len({r["repository"] for r in integration["rows"]}) == 100, "duplicated integration subject")
    require(integration["counts"] == {"pass": 99, "safe_refusal": 1, "failure": 0} and integration["gate_pass"] is True, "integration result mismatch")
    counts = Counter(r["status"] for r in integration["rows"])
    require(dict(counts) == {k: v for k, v in integration["counts"].items() if v}, "integration row totals differ")
    from tools.validate_release_prerequisites import _validate_commercial
    selection = read_json(ROOT / "commercial/corpus/selection-v1.json")
    _validate_commercial(integration, candidate_sha=MAIN, version="0.5.1", selection=selection, repositories=selection["repositories"])
    package = read_json(PACKAGE_RECEIPT)
    require(package["status"] == "PASS" and package["license_expression"] == "MIT" and package["commercial_release_qualified"] is False, "invalid package qualification scope")
    for filename, identity in package["files"].items():
        path = PACKAGE_DIRECTORY / filename
        require(path.stat().st_size == identity["bytes"] and digest(path) == identity["sha256"], "package bytes drifted")
    binding = read_json(SQJ / "evidence/main-source-binding.json")
    validate_binding(binding, SQJ / "source-final", SQJ / "source-ci-final")
    return {"windows": windows, "github_matrix": matrix,
            "integration": {"attempted": 100, "counts": integration["counts"], "codex_version": integration["codex_version"], "sha256": digest(integration_path)},
            "package_receipt_sha256": digest(PACKAGE_RECEIPT), "tested_main": MAIN,
            "named_windows_traceability_checks": list(TRACEABILITY_CASES)}


def motivating_pilot():
    directory = SQJ / "evidence/whole-task-pilot-2"
    raw = read_json(directory / "summary.json")
    require(raw["pilot_pass"] is True and raw["source_stable"] is True and raw["source_before"] == raw["source_after"], "motivating pilot was not stable and passing")
    records = raw["records"]
    require([r["trajectory"] for r in records] == [1, 2], "motivating pilot trajectory denominator changed")
    hits = []
    for trajectory in records:
        require([r["index"] for r in trajectory["rows"]] == list(range(7)), "motivating pilot request denominator changed")
        for row in trajectory["rows"]:
            index = row["index"]
            require(row == read_json(directory / f"trajectory-{trajectory['trajectory']}-request-{index}.json"), "motivating pilot summary/raw mismatch")
            expected_exit = 1 if index in (3, 4) else 0
            require(row["direct"]["exit_code"] == row["shadow"]["exit_code"] == row["whole_task"]["exit_code"] == expected_exit, "motivating pilot outcome disagreement")
            expected_status = "HIT_REUSED" if index in (1, 2, 5, 6) else "MISS_FAILED" if index in (3, 4) else "MISS_EXECUTED"
            require(row["whole_task"]["status"] == expected_status, "motivating pilot behavior mismatch")
            if expected_status == "HIT_REUSED":
                hits.append(row["whole_task"])
    require(len(hits) == 8, "motivating pilot hit denominator mismatch")
    return {"hits": len(hits), "snapshot_mean_seconds": statistics.mean(numeric(r["phase_ms"]["snapshot_prepare"]) for r in hits)/1000,
            "hit_mean_seconds": statistics.mean(numeric(r["request_wall_ms"]) for r in hits)/1000,
            "summary_sha256": digest(directory / "summary.json"), "scope": "pre-optimization motivating pilot, not pooled with final comparison"}


def render(summary, engineering):
    rows = summary["rows"]
    n = summary["total_requests"]
    agree = sum(r["whole_task_exit_agreements"] for r in rows)
    behavior = sum(r["expected_cache_behaviors"] for r in rows)
    lo, hi = min(r["warm_snapshot_latency_reduction_percent"] for r in rows), max(r["warm_snapshot_latency_reduction_percent"] for r in rows)
    ratios = [r["request_speedup"] for r in rows]
    feasible_crossovers = sorted((r for r in rows if r["modeled_hit_fraction_crossover"] is not None and 0 <= r["modeled_hit_fraction_crossover"] <= 1),
                                key=lambda r: r["modeled_hit_fraction_crossover"])
    crossover_text = ""
    if feasible_crossovers:
        low_cross, high_cross = feasible_crossovers[0], feasible_crossovers[-1]
        crossover_text = (f"The descriptive cost model yields a feasible break-even hit fraction for {len(feasible_crossovers)}/{len(rows)} subjects, "
                          f"ranging from {100*low_cross['modeled_hit_fraction_crossover']:.1f}\\% for {tex(low_cross['workload'])} "
                          f"to {100*high_cross['modeled_hit_fraction_crossover']:.1f}\\% for {tex(high_cross['workload'])}. "
                          "These crossovers use category-specific observed costs, not a measured or predicted agent repeat frequency.\n\n")
    color = next(r for r in rows if r["workload"] == "colorama")
    more = next(r for r in rows if r["workload"] == "more-itertools")
    order_caveat = (f"The order-specific results limit a broad interpretation of the aggregate ratios. Colorama's direct/fast ratio is "
                    f"{color['trajectory_totals_ms'][0]['direct']/color['trajectory_totals_ms'][0]['fast']:.2f} in the first trajectory but "
                    f"{color['trajectory_totals_ms'][1]['direct']/color['trajectory_totals_ms'][1]['fast']:.2f} in the reversed one; "
                    f"its longest direct request lasted {color['direct_request_max_ms']/1000:.2f} seconds. "
                    f"For more-itertools, optimized whole-sequence time is {100*(more['totals_ms']['fast']/more['totals_ms']['snapshot']-1):.1f}\\% higher than snapshot-first time despite faster hits. "
                    f"That subject's direct requests range from {more['direct_request_min_ms']/1000:.2f} to {more['direct_request_max_ms']/1000:.2f} seconds, "
                    f"and its snapshot-first non-hit requests from {more['snapshot_nonhit_min_ms']/1000:.2f} to {more['snapshot_nonhit_max_ms']/1000:.2f} seconds. "
                    "No such observation is removed. The experiment does not identify the cause of this dispersion or attribute the entire sequence difference to the new lookup probe. "
                    "It supports lower conditional hit latency, not uniform complete-sequence improvement over the original implementation.\n\n")
    abstract = (f"Whole-task outcomes agreed in {agree}/{n} checks. Warm-hit latency fell by {lo:.1f}--{hi:.1f}\\% relative to snapshot-first reuse, "
                f"while complete direct-to-optimized cost ratios ranged from {min(ratios):.2f} to {max(ratios):.2f}.")
    subjects = table("Fixed public-library targets. Complete source identifiers and target paths are retained in the artifact; these are convenience subjects, not a population sample.", "tab:subjects", "llr", "Library & Frozen commit prefix & Target files",
                     [f"{tex(w[0])} & \\code{{{w[2][:12]}}} & {len(w[4])}" for w in WORKLOADS])
    matching = sum(r["testmon_complete_reconstructions"] for r in rows)
    unknown = sum(r["testmon_uncovered_node_observations"] for r in rows)
    mismatch = sum(r["testmon_mismatched_node_observations"] for r in rows)
    results = (f"Across the fixed cohort, {agree}/{n} whole-task comparisons agreed with independent fresh command outcomes, "
               f"and {behavior}/{n} requests exhibited the predeclared seed, hit, or non-cacheable failure behavior. "
               f"The optimized arm returned {sum(r['optimized_hit_requests'] for r in rows)} hits; the {sum(r['optimized_nonhit_requests'] for r in rows)} non-hit requests remain in the cost denominator. "
               f"The corresponding independent full-target captures contained {sum(r['oracle_node_observations'] for r in rows):,} node observations; these repeated nodes are not independent subjects.\n\n"
               f"Conditional hit latency decreased by {lo:.1f}--{hi:.1f}\\% relative to snapshot-first reuse. "
               f"For complete sequences, {sum(r > 1 for r in ratios)}/{len(rows)} libraries favored optimized reuse over direct execution; "
               f"the per-library direct/fast ratios ranged from {min(ratios):.2f} to {max(ratios):.2f}. "
               "A value below one indicates that direct execution was faster, despite the benefit on eligible hits. "
               "Tables~\\ref{tab:sequence} and \\ref{tab:warm} show complete and conditional cost; "
               "Table~\\ref{tab:agreement} gives the agreement ledger. Setup cost appears in Table~\\ref{tab:setup}, "
               "and order sensitivity in Table~\\ref{tab:order}.\n\n" + crossover_text + order_caveat +
               f"The specified prior-PASS reconstruction of Testmon's selected results matched the complete fresh capture in {matching}/{summary['total_testmon_comparative_requests']} comparable requests. "
               f"It left {unknown:,} node observations uncovered and produced {mismatch:,} mismatched or absent-node claims. "
               "Uncovered outcomes are a contract-coverage observation, not by themselves evidence of an incorrect Testmon selection. "
               "The full raw selected outcomes remain available for alternative analyses. "
               "More-itertools has no complete Testmon comparison: its original failing-source request exceeded the 600-second harness limit. "
               "Its complete 14-request whole-task block was rerun separately without Testmon; dashes in the tables mean unavailable, not zero latency or successful selection. "
               "The successful prefix and timeout of the original attempt remain archived. Because the ordinary helper permits 900 seconds, "
               "the timeout is not used as a matched-budget superiority result.\n\n"
               f"Testmon's cohort-wide installation took {summary['testmon_shared_install_ms']/1000:.2f} seconds. "
               f"The separate corrected packaging block incurred an additional {summary['packaging_correction']['additional_testmon_install_ms']/1000:.2f}-second installation, "
               f"and replaced all {summary['packaging_correction']['original_infrastructure_error_requests']} original packaging requests whose Testmon configuration failed. "
               "The intermediate stock-plugin correction also failed under the project's warning-as-error policy; it is retained separately. "
               "Only the exact stock-plugin rewrite warning is filtered in the final Testmon configuration. "
               "Those failed requests are archived, not combined with the corrected block or selected by measured performance.")
    complete = summary["current_revision"]["rows"]
    hist = (f"The exact-main fine-grained revision evaluation completed all five selected subjects on GitHub-hosted Linux continuous-integration (CI) runners (Table~\\ref{{tab:historical}}). "
            f"Across those five subjects, all {sum(r['comparisons_pass'] for r in complete)} fresh comparisons and "
            f"{sum(r['adverse_checks_pass'] for r in complete)} adverse checks passed, but "
            f"{sum(r['reused_nodes'] for r in complete)} of {sum(r['total_node_observations'] for r in complete):,} node observations were reused. "
            f"Complete cost ratios ranged from {min(r['primary_speedup'] for r in complete):.3f} to {max(r['primary_speedup'] for r in complete):.3f}; "
            "none met the unchanged product performance gate. These current-core observations come from separate CI hosts and are not pooled with VM timings. "
            "The source archive binds the exact Git bytes used by CI. The earlier VM revision campaign, which stopped on a missing more-itertools Git object after four completed subjects, remains archived as incomplete; "
            "it is not silently overwritten or relabeled as this completed rerun.\n\n" +
            table("Current-core fine-grained revision evaluation on separate CI hosts. Each subject has 20 transitions and two execution orders. Negative p95 reduction indicates slower execution.", "tab:historical", "lrrr", "Library & Reused/observed nodes & Direct/ZeroRun & p95 reduction",
                  [f"{tex(r['workload'])} & {r['reused_nodes']}/{r['total_node_observations']} & {r['primary_speedup']:.3f} & {r['p95_reduction_percent']:.1f}\\%" for r in complete]))
    w = engineering["windows"]
    regression = (f"Fresh host regression reported {w['passed']} passed and {w['skipped']} skipped on Windows CPython 3.14.6. "
                  f"That run additionally reported {w['subtests passed']} passed subtests, separately from primary test cases, "
                  "and its source snapshot remained unchanged. Skips remain skips, not successful executions. "
                  "Table~\\ref{tab:regression} reports the five independent Linux CI checks on the delivered core commit \\code{86f4228}. "
                  "The full product performance workflow is a separate gate and is not certified by those regression jobs.\n\n" +
                  table("Independent Linux CI regression on the optimized core commit. Subtests are shown separately to avoid inflating the primary-test count.", "tab:regression", "lrrr", "Python & Passed & Skipped & Passed subtests",
                        [f"{v} & {r['passed']} & {r['skipped']} & {r['subtests passed']}" for v, r in engineering['github_matrix'].items()]) +
                  f"\n\nThe same core commit's integration smoke attempted 100 distinct repositories: 99 completed integrations, one proof-backed safe refusal, and zero failures, using {tex(engineering['integration']['codex_version'])}. "
                  "No model API was invoked. Installable version-0.5.1 distributions passed exact source-content, MIT metadata, and isolated installed-CLI checks.")
    conclusion = (f"The controlled comparison retained {agree}/{n} whole-task agreements and measured {lo:.1f}--{hi:.1f}\\% lower conditional hit latency than snapshot-first reuse. "
                  f"The complete sequence favored optimized reuse over direct execution in {sum(r > 1 for r in ratios)}/5 fixed subjects, "
                  "demonstrating why miss cost and reuse opportunity remain central to deployment.")
    pilot = summary["motivating_pilot"]
    motivation = (f"In the preliminary pycparser whole-task pilot, private snapshot construction averaged {pilot['snapshot_mean_seconds']:.2f} seconds "
                  f"within a {pilot['hit_mean_seconds']:.2f}-second mean response over {pilot['hits']} cache hits, although those hits executed no repository code.")
    return {"ABSTRACT_RESULTS": abstract, "PILOT_MOTIVATION": motivation, "SUBJECT_TABLE": subjects, "RESULTS_TEXT": results,
            "RESULTS_TABLES": numerical_tables(summary), "HISTORICAL_RESULTS": hist,
            "REGRESSION_RESULTS": regression, "CONCLUSION_RESULTS": conclusion}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    summary = analyze(SQJ / "evidence/comparison-final-1", SQJ / "source-final")
    summary["historical_campaign"] = historical_partial(SQJ / "evidence/campaign-1")
    summary["current_revision"] = current_revision()
    summary["motivating_pilot"] = motivating_pilot()
    engineering = engineering_evidence()
    document = (SQJ / "paper/submission.tex.in").read_text(encoding="utf-8")
    for key, value in render(summary, engineering).items():
        slot = "@@" + key + "@@"
        require(document.count(slot) == 1, "missing or duplicate manuscript slot")
        document = document.replace(slot, value)
    require("@@" not in document, "unresolved manuscript slot")
    abstract = re.search(r"\\abstract\{(.*?)\}\s*\\keywords", document, re.S).group(1)
    require(150 <= len(abstract.split()) <= 250, "journal abstract word count: " + str(len(abstract.split())))
    require("\\cite" not in abstract and "\\cite" not in document.split("\\section{Conclusion}")[1].split("\\section*")[0], "abstract/conclusion citation restriction")
    outputs = {SQJ / "paper/main.tex": document,
               SQJ / "generated/submission-evidence.json": json.dumps({"comparison": summary, "engineering": engineering}, indent=2, sort_keys=True, allow_nan=False) + "\n"}
    for path, content in outputs.items():
        if args.check:
            require(path.read_text(encoding="utf-8") == content, "generated submission drift: " + str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "PASS", "requests": summary["total_requests"], "abstract_words": len(abstract.split()), "meaning": "source/receipt/arithmetic/document checks; not a publication or product qualification decision"}))


if __name__ == "__main__":
    main()
