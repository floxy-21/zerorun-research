"""Build the SoftwareX article from independently reconciled evidence.

Layout previews are visibly incomplete and never written to final PDF paths.
Final builds require complete replication evidence and a pinned public release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from research.sqj.analyze_comparison import analyze as original_analysis
from research.sqj.build_submission import engineering_evidence
from research.sqj.analyze_current_revision import analyze as revision_analysis
from research.sqj.strengthening.validate_traces import build_summary as trace_analysis
from research.sqj.strengthening.analyze_replication import analyze as replication_analysis
from research.sqj.strengthening.analyze_recovered_replication import analyze as recovered_replication_analysis
from research.sqj.strengthening.analyze_state_rejoin import analyze as state_analysis
from research.softwarex import analysis_reproduction

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "research/softwarex"
SQJ = ROOT / "research/sqj"
EVIDENCE = SQJ / "strengthening/evidence"
PUBLIC = "https://github.com/floxy-21/zerorun-research"


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def bibliography(document):
    keys = set()
    for match in re.finditer(r"\\cite\{([^}]+)\}", document):
        keys.update(match.group(1).split(","))
    found = {}
    for path in (HERE / "base-references.bib", HERE / "related-work-additions.bib", HERE / "paper/dataset-reference.bib"):
        raw = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?ms)^@\w+\{([^,]+),.*?(?=^@\w+\{|\Z)", raw):
            key, block = match.group(1), match.group(0).strip()
            if key in keys:
                require(key not in found, "duplicate bibliography key")
                found[key] = re.sub(r"\{\\url\{([^}]+)\}\}", r"{\1}", block)
    require(set(found) == keys, "missing cited references: " + str(keys - set(found)))
    return "\n\n".join(found[key] for key in sorted(found)) + "\n"


def inventory_check():
    path = EVIDENCE / "strengthening-linux-2.xml"
    cases = list(ET.parse(path).iter("testcase"))
    require(len(cases) == 54 and not any(any(t.find(tag) is not None for tag in ("failure", "error", "skipped")) for t in cases), "Linux boundary/driver tests not all passing")
    return {"junit_sha256": digest(path), "cases": 54, "inventory_cases": 38, "driver_cases": 16}


def extension_evidence(preview=False):
    from research.softwarex.build_extension_evidence import build as reconcile_extension
    evidence = reconcile_extension(ROOT, preview=preview)
    if not preview:
        require(evidence["completed"] is True and evidence["preview"] is False,
                "completed extension reconciliation required")
        require(load(HERE / "generated/extension-evidence-v1.json") == evidence,
                "extension evidence is stale")
    return evidence


def application_evidence(preview=False):
    from research.softwarex.build_application_evidence import build as reconcile_application
    if preview:
        return None
    evidence = reconcile_application(ROOT)
    require(evidence["completed"] is True
            and load(HERE / "generated/application-evidence-v1.json") == evidence,
            "application evidence is incomplete or stale")
    return evidence


def application_text(evidence):
    if evidence is None:
        return "[Layout preview: application evidence pending.]"
    v2, v3 = evidence["model_backed_application"], evidence["model_backed_application_v3"]
    require(v2["live_turns_completed"] == 2 and v2["live_turns_passed"] == 1
            and v2["all_planned_checks_pass"] is False,
            "second client trial narrative differs")
    require(v3["core_lifecycle_pass"] is True and v3["live_turns_completed"] == 5
            and v3["live_turns_passed"] == 4 and v3["decision_turns_passed"] == 0
            and v3["fresh_oracle_calls"] == v3["fresh_oracle_calls_passed"] == 2
            and v3["interpretation_cases_completed"] == 0
            and v3["all_planned_checks_pass"] is False,
            "third client trial narrative differs")
    guided = evidence["guided_model_application"]
    require(guided["all_planned_checks_pass"] is True
            and guided["live_decision_turns_completed"] == guided["live_decision_turns_passed"] == 2
            and guided["interpretation_cases_completed"] == guided["interpretation_cases_passed"] == 6
            and guided["fresh_oracle_calls"] == 2 and guided["fresh_oracles_pass"] is True
            and guided["non_model_setup_tool_calls"] == 2,
            "guided client narrative differs")
    installation = evidence["public_guide_installation"]["validation"]
    quickstart = evidence["public_guide_quickstart"]["validation"]
    require(installation["passed"] is True and quickstart["passed"] is True
            and quickstart["runtime_files"] == 36,
            "passing public quickstart narrative required")
    return (
        "Client evaluation separated API execution from model-selected use. A scripted consumer passed 14 response cases; "
        "11 installed-server checks covered eight discovery/diagnostic requests. All model attempts are retained. "
        "Trial~1 stopped at missing authority. Trial~2 forwarded the authority path and passed readiness, but Codex denied "
        "its next call because client approval was unavailable. With explicit, tool-specific operator preapproval, "
        "trial~3 completed four Codex~0.153.3 turns: readiness, fresh success, identified reuse and fresh verification. "
        "All four interpretations were correct; two separate fresh container executions agreed. The fifth, model-selected "
        "request invented an unsupported argument and was rejected before execution. The model declined success, "
        "but the protocol failed and stopped; the remaining decision and six interpretation cases were not run. "
        "Thus the core lifecycle passed, not the entire trial. "
        "The sparse input-parameter description motivated a separately recorded documented-use example; "
        "it does not establish the cause of the model error. "
        "That prospectively frozen demonstration supplied one neutral API card to both decision prompts. "
        "After non-model readiness and seeding, the model selected normal reuse for a status-only need and fresh "
        "verification for a fresh-evidence need; both decisions and two independent fresh checks passed. "
        "Six fixed no-tool interpretation cases also passed, including fresh failure, refusal, and rejecting a hit "
        "when fresh diagnostics were required. All eight model turns are retained, without retries. "
        "This documents bounded use, not a causal documentation effect or population accuracy. "
        "An account-free laboratory guide additionally exercises missing-authority refusal, authorized readiness, "
        "fresh success, reuse and verification through the actual server. After an external-adapter check, "
        "a clean public clone and new external installation reproduced the published procedure with 36 matching runtime modules. "
        f"The recorded installation took {installation['elapsed_seconds']:.1f}~s and the server check {quickstart['elapsed_seconds']:.1f}~s, "
        "excluding cloning, prior Docker setup and researcher preparation. These are researcher-executed checks, not independent user observations.")


def extension_text(evidence):
    client = evidence["scripted_client_conformance"]
    require(client["scripted_cases"] == client["scripted_passed"] == 14
            and client["installed_stdio_requests"] == 8
            and client["live_checks"] == client["live_checks_passed"] == 11,
            "client conformance narrative denominator differs")
    client_text = (
        "A scripted consumer passed 14 response cases across nine categories, separating previous success, fresh success, failure and refusal, and rejecting inconsistent metadata. "
        "Positive cases are preserved API results in explicitly scripted MCP-shaped fixtures, not live agent responses. "
        "A separately installed server passed 11 checks across eight actual stdio requests, covering discovery, diagnostics and refusal; those requests did not execute user code. ")
    live = evidence["bounded_live_client"]
    if live["functional_lifecycle_pass"]:
        require(live["recorded"] is True and live["agent_stages_recorded"] == 4
                and len(live["independently_validated_stages"]) == 4,
                "passing live narrative lacks four validated stages")
        client_text += (
            "A fresh Linux installation additionally completed four constrained Codex 0.153.3 turns: one diagnostic call, then "
            "\\code{MISS\\_EXECUTED}, \\code{HIT\\_REUSED} and \\code{VERIFY\\_MATCH} on one reviewed synthetic fixture. "
            "Each turn made one MCP call, with no retries; the three execution responses shared one cache key. ")
    elif live["recorded"]:
        adverse = live.get("stage_adverse_outcomes", [])
        diagnostic = adverse[0].get("raw_diagnostics", {}) if len(adverse) == 1 else {}
        if diagnostic.get("classification") == "untrusted_doctor_readiness_refusal":
            require(live["agent_stages_recorded"] == 1 and diagnostic["mcp_calls"] == 1
                    and diagnostic["run_tests_calls"] == 0 and diagnostic["exact_marker_only"] is False,
                    "live refusal narrative differs")
            client_text += (
                "The bounded Codex attempt stopped after one diagnostic call: the server reported no matching manifest authority and remained observe-only. "
                "An additional commentary message also violated the prespecified exact-message harness; no execution, reuse or verification turn followed, and no retry was made. ")
        else:
            client_text += (f"The separate bounded Codex lifecycle did not complete successfully; {live['agent_stages_recorded']} agent turns were recorded. "
                            "The failure receipt is retained and no successful live lifecycle is claimed. ")
    else:
        client_text += "[Layout preview: live-client outcome pending.] "
    client_text += (
        "The research adapter and pre-model packaging corrections are separately source-bound; the runtime and authority checks are unchanged. "
        "This tests a client integration, not agent understanding, issue resolution or workflow speedup. No external user study is implied.")
    handoff = evidence.get("explicit_trust_path_diagnostic")
    if handoff and handoff["recorded"]:
        if handoff["passed"]:
            require(handoff["negative_control_passed"] is True and handoff["stages_recorded"] == 5
                    and handoff["stages_validated"] == 5 and handoff["model_called"] is False
                    and handoff["codex_correction_tested"] is False, "configuration diagnostic narrative differs")
            client_text += (
                " A separately frozen, non-model diagnostic tested the trust-path handoff: its missing-variable control refused readiness, "
                "while explicitly supplying the same external authority path enabled readiness, fresh execution, reuse and verification in five checked calls. "
                "This validates the server configuration correction, not a corrected Codex run; the original live failure remains unchanged.")
        else:
            client_text += " A separate non-model trust-path diagnostic was also recorded without establishing a completed corrected lifecycle."
    operating = evidence["operating_region"]
    subjects = operating["subjects"]
    require([row["workload"] for row in subjects] == ["click", "pycparser", "colorama", "packaging"],
            "operating-region subjects differ")
    require(all(row["crossover"]["regime"] == "reuse_faster_above_equality" for row in subjects),
            "operating-region direction changed")
    require(abs(operating["constructed_hit_fraction"] - 4 / 7) < 1e-12,
            "constructed hit fraction changed")
    crossovers = ", ".join(f"{100 * row['crossover']['equality_hit_fraction']:.2f}\\%" for row in subjects)
    fractions = [row["hit_cost_attribution"]["fractions_of_outer"]["two_fingerprints"] for row in subjects]
    operating_text = (
        "A post-hoc operating-region analysis retains every completed block and holds within-category composition fixed. "
        "Let $D_h,F_h$ and $D_m,F_m$ denote mean complete direct/optimized costs in hit and non-hit categories. "
        "For a hypothetical hit fraction $p$, fixed category means give the equality point\n"
        "\\[p^*=\\frac{F_m-D_m}{(F_m-D_m)+(D_h-F_h)}.\\]\n"
        f"The crossovers for Click, pycparser, Colorama and Packaging are {crossovers}, respectively; larger fractions favor reuse in this fixed-mixture calculation. "
        "The imposed fraction is 57.14\\%, explaining why full-sequence ratios can lie near or below one despite fast hits. "
        "All six block-level crossovers per subject are archived; their ranges are descriptive, not confidence intervals. "
        f"The two fingerprints account for {100 * min(fractions):.1f}--{100 * max(fractions):.1f}\\% of optimized hit time; their inclusive enclosing timer is not double-counted. "
        "This is not a measured agent hit frequency or a deployable admission policy. Different miss/failure mixtures and host costs change the threshold. "
        "Common setup cancels from equality; additional deployment/review costs remain unmeasured, not zero.")
    return client_text, operating_text


def build(preview=False):
    if not preview:
        analysis_reproduction.require_canonical_python()
    original = original_analysis(SQJ / "evidence/comparison-final-1", SQJ / "source-final")
    engineering = engineering_evidence()
    revision = revision_analysis()
    trace = trace_analysis()
    require(load(EVIDENCE / "trace-summary-v1.json") == trace, "trace summary stale")
    require(trace["main"]["selected_episode_rows"] == 128 and trace["main"]["valid_episode_rows"] == 122, "trace denominator differs from narrative")
    main_trace = trace["main"]
    require(main_trace["valid_distinct_repositories"] == 104 and main_trace["valid_distinct_issues"] == 120, "trace clustering differs")
    require(main_trace["repeat_pairs"] == 120 and main_trace["no_intervening_barrier_pair_count"] == 0, "repeat narrative changed")
    require(engineering["windows"]["passed"] == 1593 and engineering["windows"]["skipped"] == 55 and engineering["windows"]["subtests passed"] == 19, "engineering narrative drift")
    require(engineering["integration"]["counts"] == {"failure": 0, "pass": 99, "safe_refusal": 1}, "integration narrative drift")
    require(sum(r["comparisons_pass"] for r in revision["rows"]) == 200 and sum(r["reused_nodes"] for r in revision["rows"]) == 0 and not revision["all_product_gates_pass"], "revision narrative drift")
    inventory = inventory_check()
    rows = original["rows"]
    reductions = [r["warm_snapshot_latency_reduction_percent"] for r in rows]
    ratios = [r["request_speedup"] for r in rows]
    more = next(r for r in rows if r["workload"] == "more-itertools")
    original_text = (
        f"Two reversed-order seven-request sequences per library yielded {sum(r['whole_task_exit_agreements'] for r in rows)} fresh-result agreements and {sum(r['optimized_hit_requests'] for r in rows)} optimized-arm whole-task hits. "
        f"The original mean conditional hit latency reduction against the ablation was {min(reductions):.1f}--{max(reductions):.1f}\\%. "
        f"Complete-sequence direct/optimized ratios were {min(ratios):.2f}--{max(ratios):.2f}. "
        f"For more-itertools, optimized execution was {100*(more['totals_ms']['fast']/more['totals_ms']['snapshot']-1):.1f}\\% slower overall than snapshot-first reuse, despite faster hits.")
    publication = load(HERE / "generated/public-release.json") if (HERE / "generated/public-release.json").exists() else None
    if preview:
        commit = publication["code_commit"] if publication else "main"
        replication = None
        abstract = "[Layout preview: the prospectively planned replication is still running.]"
        result = "[Layout preview only. Complete replication results are required before finalization.]"
        table = "Pending & --- & --- & --- & --- \\\\"
    else:
        require(publication and publication["public_verified"] is True and publication["repository_url"] == PUBLIC, "verified public release required")
        commit = publication["code_commit"]
        require(re.fullmatch(r"[0-9a-f]{40}", commit), "invalid pinned public source commit")
        recovered = (EVIDENCE / "short-randomized-recovery-v1").is_dir()
        require(recovered, "this final manuscript requires the explicit retained recovery evidence")
        replication = (recovered_replication_analysis(EVIDENCE / "short-randomized-replication-v1",
            EVIDENCE / "short-randomized-recovery-v1", SQJ / "source-final") if recovered else
            replication_analysis(EVIDENCE / "short-randomized-replication-v1", SQJ / "source-final"))
        replication = analysis_reproduction.reconcile_saved_analysis(replication, ROOT)
        require(replication["completed"], "incomplete replication cannot become final paper")
        compact = replication["paper_summary"]["subjects"]
        abstract = "The four-target study, completed through an explicit post-crash recovery, reconciles 168 fresh comparisons and 96 reused successes."
        result = (
            "Across 24 completed planned blocks, all 168 requests agreed with their fresh full-target checks and showed the expected cache behavior, including 96 optimized hits and 72 non-hit requests. "
            "Table~\\ref{tab:replication} reports full-sequence and setup-inclusive ratios, all-block spread, and conditional median-hit savings separately. No completed block or outlying invocation within those blocks is dropped; interrupted-attempt costs are reported separately. "
            "The complete-block record contains 504 timed arm invocations and 168 separate fresh-oracle captures. The original five-subject results are not pooled with this extension. "
            "A VirtualBox host assertion interrupted execution after 21 complete blocks and part of Packaging block four. A recorded amendment reran only the unfinished preselected blocks four through six using the unchanged driver. Original failures, partial records, crash remnants and recovery preflight failures remain available. This is recovered coverage, not uninterrupted completion of the no-retry protocol. Packaging's unflushed original setup total is unavailable, so its setup-inclusive ratio is omitted. "
            f"The interrupted attempt additionally retains {replication['additional_interrupted_complete_requests']} complete request, {replication['additional_incomplete_requests']} partial request and an outcome-less invocation directory. Including its surviving arm costs changes Packaging's direct/optimized ratio to {next(r for r in replication['subjects'] if r['workload'] == 'packaging')['all_recorded_attempt_direct_to_fast_ratio']:.3f}.")
        table_rows = []
        for r in compact:
            setup = r['setup_inclusive_direct_to_fast_ratio']
            setup_text = "---" if setup is None else f"{setup:.2f}"
            table_rows.append(f"{r['workload']} & {r['direct_to_fast_ratio']:.2f} & {setup_text} & {r['block_direct_to_fast_range'][0]:.2f}--{r['block_direct_to_fast_range'][1]:.2f} & {r['median_hit_latency_reduction_percent_vs_snapshot']:.1f} \\\\")
        table = "\n".join(table_rows)
    state_text = "An exact inverse-edit candidate is documented separately as a reconstruction plan; it is not counted as an observed cache hit."
    state_binding = None
    state_path = HERE / "paper/state-rejoin.tex"
    if state_path.exists():
        require((HERE / "generated/state-rejoin-review.json").is_file(), "unreviewed state reconstruction text")
        review = load(HERE / "generated/state-rejoin-review.json")
        require(review["text_sha256"] == digest(state_path) and review["verified"] is True, "state reconstruction review mismatch")
        analysis_path = EVIDENCE / "state-rejoin-analysis-v1.json"
        case_directory = review.get("case_directory", "agent-state-rejoin-v1")
        require(case_directory in {"agent-state-rejoin-v1", "agent-state-rejoin-v2", "agent-state-rejoin-v3"}, "unsupported state case directory")
        verified_state = state_analysis(EVIDENCE / case_directory, evidence=EVIDENCE,
            replication_directory=EVIDENCE / "short-randomized-replication-v1", engine_archive=SQJ / "source-final")
        require(verified_state["completed"] is True and verified_state == load(analysis_path), "state analysis incomplete or stale")
        require(review["analysis_sha256"] == digest(analysis_path), "state review analysis binding differs")
        require(verified_state["requests"] == 4 and verified_state["optimized_hits"] == 1, "state narrative denominator changed")
        state_binding = {"analysis_sha256": digest(analysis_path), "review_sha256": digest(HERE / "generated/state-rejoin-review.json"),
            "text_sha256": digest(state_path), "independent_analysis": verified_state}
        state_text = state_path.read_text(encoding="utf-8").strip()
    extension = extension_evidence(preview)
    _, operating_text = extension_text(extension)
    application = application_evidence(preview)
    client_text = application_text(application)
    slots = {"PUBLIC_COMMIT": commit, "ABSTRACT_RESULT": abstract, "REPLICATION_RESULT": result,
             "REPLICATION_ROWS": table, "STATE_REJOIN": state_text, "ORIGINAL_RESULTS": original_text}
    slots.update({"CLIENT_EVIDENCE": client_text, "OPERATING_REGION": operating_text})
    source = HERE / "paper/submission.tex.in"
    document = source.read_text(encoding="utf-8")
    for key, value in slots.items():
        require("@@" + key + "@@" in document, "missing manuscript slot")
        document = document.replace("@@" + key + "@@", value)
    require("@@" not in document, "unresolved manuscript slot")
    sections = re.findall(r"\\section\{([^}]+)\}", document)
    require(sections == ["Motivation and significance", "Software description", "Illustrative examples", "Impact", "Conclusions"], "SoftwareX mandatory sections changed")
    abstract_text = document.split("\\begin{abstract}")[1].split("\\end{abstract}")[0]
    require(len(abstract_text.split()) <= 150, "abstract exceeds local short-abstract bound; template requests approximately 100 words")
    refs = bibliography(document)
    summary = {"schema": "zerorun.softwarex-paper.v1", "preview": preview, "public_release": publication,
        "template_sha256": digest(source), "main_tex_sha256": hashlib.sha256(document.encode()).hexdigest(),
        "bibliography_sha256": hashlib.sha256(refs.encode()).hexdigest(),
        "original_comparison": original, "engineering": engineering, "fine_grained_revision": revision,
        "replication": replication, "state_rejoin": state_binding, "trace_summary_sha256": digest(EVIDENCE / "trace-summary-v1.json"),
        "inventory": inventory, "extension": extension, "application": application,
        "abstract_whitespace_words": len(abstract_text.split()),
        "meaning": "Evidence and format validation, not an acceptance probability or production qualification."}
    return document, refs, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document, refs, summary = build(args.preview)
    destination = ROOT / "tmp/pdfs/softwarex-layout" if args.preview else HERE / "paper"
    generated = destination if args.preview else HERE / "generated"
    outputs = {destination / "main.tex": document, destination / "references.bib": refs,
               generated / "paper-evidence.json": json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"}
    for path, content in outputs.items():
        if args.check:
            require(path.read_bytes() == content.encode(), "generated file stale: " + str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"validated": True, "preview": args.preview, "abstract_words": summary["abstract_whitespace_words"], "destination": str(destination)}))


if __name__ == "__main__":
    main()
