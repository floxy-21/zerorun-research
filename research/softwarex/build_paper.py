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
TITLE = "ZeroRun: Reproducible test-result reuse for AI coding tools"


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


def handoff_evidence(preview=False):
    """Only reconciled, current records can populate application claims."""
    if preview:
        return None
    from research.softwarex.build_handoff_evidence import build as reconcile_handoffs
    evidence = reconcile_handoffs(ROOT)
    require(evidence["available_completed_runs_reconciled"] is True
            and load(HERE / "generated/handoff-evidence-v1.json") == evidence,
            "handoff evidence is incomplete or stale")
    return evidence


def qualification_example(agent, commit):
    relative = (Path(agent["evaluation_path"]) / "cases/tobymao__sqlglot-3425"
                / "block-0/zerorun/setup.json")
    setup = load(ROOT / relative)
    manifest = setup["manifest"]
    task = manifest["tasks"]["handoff-tests"]
    require(manifest["version"] == 2 and task["command"] == ["/usr/local/bin/python", "-m", "pytest",
            "-p", "no:cacheprovider", "tests/dialects/test_mysql.py"], "worked-example command differs")
    require(len(task["inputs"]) == 18 and {"sqlglot", "tests", "setup.py", "setup.cfg"} <= set(task["inputs"])
            and set(task["inputs"]) == {row["path"] for row in setup["before"]["rows"] if "/" not in row["path"]}
            and task["env"] == task["outputs"] == task["unsafe_effects"] == []
            and task["result_only"] is True and task["cache_streams"] is False
            and task["closure_reviewed"] is True and task["platform"] == "linux/amd64"
            and task["image"] == setup["setup"]["runtime_image"], "worked-example contract differs")
    link = PUBLIC + "/blob/" + commit + "/" + relative.as_posix()
    return (
        r"\paragraph{Worked qualification} The SQLGlot laboratory's \code{handoff-tests} manifest invokes:"
        "\n\\begin{verbatim}\n/usr/local/bin/python -m pytest -p no:cacheprovider \\\n"
        "    tests/dialects/test_mysql.py\n\\end{verbatim}\n"
        "The \\href{" + link + "}{manifest} declares all 18 materialized top-level entries, including "
        "\\code{sqlglot}, \\code{tests}, \\code{setup.py} and \\code{setup.cfg}, covering imports, tests, fixtures "
        "and configuration; Git/cache state is excluded. Python, pytest and pretend reside in the pinned image. "
        "It declares an empty environment allowlist, outputs and unsafe effects, with result-only status and no stream caching. "
        "ZeroRun enforces declared-input/runtime identity and authentication: declared edits invalidate reuse. "
        "The operator must establish that omitted state, randomness and time cannot affect the command; "
        "\\code{closure\\_reviewed=true} asserts this rather than proving it. Authentication cannot prove closure. "
        "Fresh diagnostics require execution; uncertain qualification requires fresh execution or refusal. "
        "This laboratory declaration is not production authorization; review effort was not timed.")


def handoff_text(evidence, commit="main"):
    if evidence is None:
        return "[Layout preview: reconciled real-repository evidence pending.]"
    runs = evidence["controlled_runs"]
    main = runs["v6_main_corrected"]
    require(main["state"] == "RECONCILED_RECORDED_OUTCOMES"
            and main["all_selected_cases_completed"] and main["complete_case_count"] == 24,
            "complete corrected-fixture V6 evidence required")
    costs = main["complete_paired_costs"]
    require(costs["complete_blocks"] == costs["cache_hits"] == 48, "V6 request denominator differs")
    repos = len({row["repo"] for row in main["cases"]})
    faster = main["complete_case_faster_count"]
    chain_saving = 100 * costs["chain_saved_fraction"]
    consumer_saving = 100 * (1 - costs["consumer_ms"]["zerorun"] / costs["consumer_ms"]["fresh"])
    paragraphs = [
        "A frozen issue-state cohort contains 24 cases across eight repositories, selected by repository shortlist, "
        "metadata checks and hash ordering before timing. Both arms receive the same reference-patched source "
        "and supplied full target. Fresh-only executes a producer and consumer; ZeroRun executes a cold producer "
        "then requests identified prior status. Each arm is checked against an independently fresh full-target "
        "oracle. Two blocks reverse arm order. These imposed handoffs do not measure natural repetition frequency.",
        f"The corrected-fixture V6 repeat completed all 24 cases across {repos} repositories: 48 paired blocks, "
        f"48 reused successes and 96 agreeing fresh-oracle checks. Descriptively, complete chain time was {chain_saving:.1f}\\% "
        f"lower and mean scripted consumer-request latency {consumer_saving:.1f}\\% lower. The original fine-grained "
        "host monitor was not recovered; a retained VirtualBox log records a 5.53~s heartbeat lapse in the nominal "
        "window with conditional clock association. These percentages do not certify uninterrupted or quiet-host "
        f"performance. Chains improved in {faster}/24 cases and worsened in {24-faster}/24. "
        "Source and runtime identities are bound before and after execution."]
    table = []
    for name,label in (("v1_pilot","Copy pilot"),("v2_pilot_repeat","Image repeat"),
                       ("v5_main_compatible","Compatible V5"),("v6_main_corrected","Corrected V6")):
        run = runs[name]
        require(run["state"] == "RECONCILED_RECORDED_OUTCOMES", "selected comparison missing")
        c = run["complete_paired_costs"]
        n = c["complete_blocks"]
        table.append(f"{label} & {run['complete_case_count']}/{run['selected_case_count']} & "
                     f"{c['chain_ms']['fresh']/1000:.2f} & {c['chain_ms']['zerorun']/1000:.2f} & "
                     f"{c['consumer_ms']['fresh']/n/1000:.2f} & {c['consumer_ms']['zerorun']/n/1000:.2f}" + r" \\")
    paragraphs.append("\n".join([
        r"\begin{table}[htbp]\centering\small",
        r"\begin{tabular}{@{}lrrrrr@{}}\toprule",
        r" & Cases & \multicolumn{2}{c}{Chain sum (s)} & \multicolumn{2}{c}{Consumer mean (s)} \\",
        r"Deployment & complete/selected & Fresh & ZeroRun & Fresh & ZeroRun \\",
        r"\midrule", *table, r"\bottomrule\end{tabular}",
        r"\caption{Separate cohorts, without pooling. Chain includes cold producer plus consumer; setup, image construction, review, fresh oracles and diagnostics are excluded. Their measured costs are separate; operator review is unmeasured. V6 is descriptive: its original host monitor is missing, and a 5.53~s heartbeat lapse has conditional clock association. No uninterrupted or quiet-host timing is certified. V5's original Lizard failure and V6's correction remain separate.}",
        r"\label{tab:handoffs}\end{table}"]))
    paragraphs.append(
        f"Adding per-arm setup gives V6 totals of {costs['setup_inclusive_chain_ms']['fresh']/1000:.2f}~s fresh "
        f"and {costs['setup_inclusive_chain_ms']['zerorun']/1000:.2f}~s ZeroRun. Common compatible-image preparation "
        "took 82.29~s separately. Its recorded Docker build disabled layer-cache reuse and network access using "
        "16 hash-bound wheels; the existing VM and base image remained prerequisites. This establishes an "
        "author-side recipe build, not clean-OS or independent-human replication, and the loopback-derived image "
        "is not a publicly pullable artifact.")
    paragraphs.append(
        "V5 completed 23 cases and retained Lizard-191 as unsupported. V6 preserves selection, command and source "
        "patch, changing only the stale expected complexity in \\code{Test\\_Big.test\\_typedef} from two to three. "
        "The \\href{https://github.com/terryyin/lizard/blob/67d87968e9fecd459c9a9a1dcb01cf6ceac5721d/test/test_languages/testCAndCPP.py#L685}"
        "{pinned upstream method} supports this explicit correction. The original 2/24 campaign, interrupted "
        "15/24 repeat and all compatibility, collection, lifecycle, assertion and capture failures remain in the "
        "coverage audit. Incomplete cases contribute no zero-cost observations.")
    repeat = runs["v2_pilot_repeat"]["complete_paired_costs"]
    paragraphs.append(
        f"The image repeat reduced consumer waiting {100*(1-repeat['consumer_ms']['zerorun']/repeat['consumer_ms']['fresh']):.1f}\\% "
        f"while increasing chain time {100*(repeat['chain_ms']['zerorun']/repeat['chain_ms']['fresh']-1):.1f}\\%. "
        "Identified earlier status can reduce waiting after producer work; newly executed diagnostics require "
        "fresh validation. Qualification and acquisition add costs. The operating guide specifies declared "
        "inputs and re-review triggers; an unchanged manifest does not qualify arbitrary future source changes.")
    paragraphs.append(
        r"The \href{https://github.com/floxy-21/zerorun-research/blob/" + commit +
        r"/research/softwarex/APPLICATION_COVERAGE_AUDIT.md}{coverage audit} and reproduction guide index every cohort, "
        "raw outcome, setup cost and intervention, including the separate earlier single-producer pilot.")
    exploratory = evidence["exploratory_amortization"]["reconciliation"]
    require(exploratory["reconciled"] and exploratory["all_six_blocks_complete"]
            and not exploratory["material_correctness_stop"], "amortization observations differ")
    blocks = exploratory["blocks"]
    require([(b["consumer_requests"], b["block"]) for b in blocks]
            == [(1,0),(1,1),(2,0),(2,1),(4,0),(4,1)], "amortization conditions differ")
    one = [100 * -b["measurements"]["chain_saved_fraction"] for b in blocks[:2]]
    four = [100 * b["measurements"]["chain_saved_fraction"] for b in blocks[4:]]
    paragraphs.append(
        "An exploratory follow-up selected LKML-85 after observing V6's cold overhead. With one consumer, "
        f"both chains were slower ({min(one):.1f}--{max(one):.1f}\\%); two consumers gave mixed results; "
        f"four gave {min(four):.1f}--{max(four):.1f}\\% lower chain cost. All six blocks and fresh checks are retained. "
        "Fixed condition order and concurrent host I/O prevent separating repetition from warm-up and host effects. "
        "This sensitivity example is not pooled with V6 or evidence of natural demand. Net time benefit must "
        "subtract additional setup, qualification and execution costs from avoided repeat execution.")
    return "\n\n".join(paragraphs)


def actual_agent_evidence(preview=False):
    if preview:
        return None
    from research.softwarex.build_agent_application_evidence import build
    result = build(ROOT)
    require(load(HERE / "generated/agent-application-053-v1.json") == result,
            "actual agent evidence is stale")
    return result


def consumer_lizard_audit_evidence(application, handoff, preview=False):
    """Reuse strict study reconciliations and bind the new reader-facing audit."""
    if preview:
        return None
    from research.softwarex import build_consumer_lizard_audit as audit
    recovered = {version: handoff["controlled_runs"][key]["reconciliation"]
                 for version, key in ((5, "v5_main_compatible"), (6, "v6_main_corrected"))}
    value = audit.build(ROOT, application=application, recovered=recovered)
    require(load(ROOT / audit.JSON_PATH) == value
            and (ROOT / audit.MARKDOWN_PATH).read_bytes() == audit.markdown(value).encode(),
            "consumer/Lizard reader audit is stale")
    counts = value["summary"]
    require(counts["consumer_rows"] == counts["literal_freshness_matches"]
            == counts["literal_outcome_matches"] == 15
            and counts["messages_with_unverifiable_wire_assertions"] == 13,
            "literal consumer audit interpretation differs")
    return {"path": audit.JSON_PATH, "sha256": digest(ROOT / audit.JSON_PATH),
            "markdown_path": audit.MARKDOWN_PATH, "markdown_sha256": digest(ROOT / audit.MARKDOWN_PATH),
            "summary": counts, "independent_human_review": False}


def agent_case_table(evidence):
    """Keep every original producer outcome and its actual consumer disposition."""
    from research.softwarex.agent_application_053.validation import CASES, STAGES
    producers = evidence["producer"]["cases"]
    require([row["case_id"] for row in producers] == list(CASES),
            "actual-case table selection or order differs")
    consumers = evidence["consumer"]["cases"]
    by_case = {row["case_id"]: row for row in consumers}
    eligible = [row["case_id"] for row in producers if row["classification"]["completed_verified_fix"]]
    require(len(by_case) == len(consumers) == 5 and list(by_case) == eligible,
            "actual-case table consumer eligibility differs")
    extra = evidence["additive_client_event_audit"]["rows"]
    table = []
    for row in producers:
        classification = row["classification"]
        fixed = classification["completed_verified_fix"]
        failed = classification["baseline_nonxfail_failed_calls"]
        require(classification["baseline_available"] and classification["final_available"]
                and classification["same_collected_node_set"] and classification["baseline_exit_code"] == 1
                and failed and len(failed) == len(set(failed))
                and classification["final_exit_code"] == (0 if fixed else 1),
                "actual-case table baseline/final meaning differs")
        disposition = "Ineligible"
        if fixed:
            stages = by_case[row["case_id"]]["stages"]
            require([item["stage"] for item in stages] == list(STAGES)
                    and all(item["model_invoked"] and item["process_completed_successfully"]
                            and item["analysis"]["model_turn_completed"] and item["analysis"]["boundary_pass"]
                            for item in stages), "actual-case table consumer turn incomplete")
            restored = [item for item in extra if item["case_id"] == row["case_id"] and item["stage"] == "restored"]
            require(stages[0]["analysis"]["reuse_observed"]
                    and any(item["classification"] == "FRESH_SUCCESS" for item in stages[1]["analysis"]["completed_mcp_results"])
                    and len(restored) == 1 and restored[0]["additional_fresh_failures"] == 1,
                    "actual-case table cannot infer unrecorded consumer results")
            disposition = "H / V / F"
        label = row["case_id"].split("__", 1)[1]
        table.append(f"{label} & {len(failed)} & {classification['final_exit_code']} & {disposition}" + r" \\")
    return "\n".join([
        r"\begin{table}[htbp]\centering\small",
        r"\begin{tabular}{@{}lrrl@{}}\toprule",
        r"Original case & Baseline failures & Final exit & Consumers \\", r"\midrule",
        *table, r"\bottomrule\end{tabular}",
        r"\caption{Six actual model-produced patches, separate from scripted reference-patch timing. Baseline failures are non-xfail failed test calls; final exit zero means the complete configured target passed. H/V/F denotes identified prior success, fresh agreement, then fresh failure after operator restoration. SQLGlot remains a primary failure; its separately assisted repair is excluded.}",
        r"\label{tab:actual-cases}\end{table}"])


def agent_application_text(evidence, commit):
    result = evidence["producer"]
    require(result["selected_cases"] == 6 and result["attempted_states"] == 12
            and result["completed_verified_fixes"] == 5 and result["error"] is None,
            "actual model-producer narrative differs")
    consumer = evidence["consumer"]
    review = evidence["consumer_interpretation_review"]
    require(consumer["eligible"] == 5 and consumer["stage_counts"]["available"]["reuse"] == 5
            and consumer["stage_counts"]["fresh"]["fresh_success"] == 5
            and evidence["additive_client_event_audit"]["additional_fresh_failures"] == 5
            and review["assessed_messages"] == review["supported_core_status_freshness_and_limits"] == 15
            and review["messages_with_unverifiable_details"] == 13,
            "actual consumer observations differ")
    relative = "research/softwarex/evidence/agent-application-053-consumers-v2/record-only/cases/eliben__pycparser-236/plan/qualification.json"
    qualification = load(ROOT / relative)
    require(qualification["command"] == ["/usr/local/bin/python", "-m", "pytest", "-p", "no:cacheprovider", "tests/test_c_parser.py"]
            and set(qualification["states"]) == {"final", "restored"}
            and qualification["restoration"]["changed_paths"] == ["pycparser/c_parser.py"]
            and qualification["inspection"]["human_review_seconds"] is None,
            "actual qualification example differs")
    guide = PUBLIC + "/blob/" + commit + "/research/softwarex/REAL_AGENT_APPLICATION_053_RESULTS.md"
    audit = PUBLIC + "/blob/" + commit + "/research/softwarex/CONSUMER_LIZARD_AUDIT.md"
    return "\n\n".join([
        "A prospective application selected six issues across six repositories. Each producer received the issue "
        "and unchanged supplied tests, without the reference production fix, for one bounded model attempt. "
        "Fresh container checks of each baseline and actual final snapshot verified five fixes with identical "
        "collected-node sets. SQLGlot's primary patch retained two failing dialect tests; a separately assisted "
        "production repair subsequently passed the same 99-node target without changing the original 5/6 outcome. "
        "Table~\\ref{tab:actual-cases} retains every primary case. Requests specified gpt-6-astra, medium effort, "
        "through Codex CLI 0.153.3; captured events did not identify "
        "the backing model independently.",
        agent_case_table(evidence),
        "Laboratory scripts qualified, externally authorized and seeded the five eligible patched states. "
        "Actual model consumers then received task-level requests about available evidence, newly executed "
        "diagnostics, and an operator-restored original buggy state. All 15 turns completed, each with one MCP "
        "request: five \\code{HIT\\_REUSED}, five \\code{VERIFY\\_MATCH}, and five fresh \\code{MISS\\_FAILED} "
        "results after restoration. In these 15 turns, recorded core interpretations matched historical/fresh "
        "and success/failure status within the configured target. Preparation and restoration were scripted; "
        "this is a guided application, not a wholly autonomous workflow.",
        "The frozen parser left five failed client envelopes unclassified; an additive audit verifies "
        "their intact payloads without rewriting records. A reproducible comparison matches literal model "
        "status/freshness fields to the recorded results; internal AI-assisted review assessed stated limits. "
        "Neither is independent human review. Thirteen messages additionally asserted wire "
        "error flags omitted from the CLI archive; those details remain unverifiable, not certified or established "
        "false. The \\href{" + audit + "}{15-row response audit} links each request, payload and interpretation "
        "with exact record locators; the \\href{" + guide + "}{application ledger} retains prompts, preparation failures and "
        "separate costs. The 415.91~s consumer-model total includes 60.68~s runner "
        "time; neither is a controlled end-to-end saving.",
        r"\paragraph{Worked qualification} The \href{" + PUBLIC + "/blob/" + commit + "/" + relative +
        "}{Pycparser record} binds \\code{tests/test\\_c\\_parser.py}, parser modules, generated tables, fixtures "
        "and configuration under the fixed image, with no forwarded host environment or reusable outputs. "
        "Inspection covers exactly the actual patched state and restoration of \\code{pycparser/c\\_parser.py}; "
        "tests remain unchanged. Any other source, command, dependency or contract change requires renewed review. "
        "\\code{closure\\_reviewed} records an assertion, not proven completeness or determinism. "
        "Expert review effort remains unmeasured."])


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
    from research.softwarex.quickstart_053 import validate_receipt_pair
    directory = HERE / "evidence/quickstart-public-053-v1"
    current = validate_receipt_pair(ROOT, directory / "install.json", directory / "check.json")
    require(current["passed"] and current["check"]["stages_recorded"] == 5, "current public quickstart differs")
    return (
        "Three Codex configurations failed overall through missing authority, approval refusal or unsupported arguments. "
        "A separate API-guided demonstration passed two model decisions and six interpretations without retries; "
        "neither an identified backing model nor a causal documentation effect was established. Full trial "
        "denominators, failures, scripted checks and fresh oracles remain in the supplement.")


def fresh_public_evidence(preview=False):
    if preview:
        return None
    from research.softwarex import verify_fresh_public_053 as reader
    relative = "research/softwarex/evidence/public-fresh-linux-053-v1"
    result = reader.verify(ROOT, ROOT / relative)
    require(result["passed"] is True and result["new_docker_empty_inventory"] is True
            and result["normal_mcp_requests"] == 10 and result["fresh_failure_requests"] == 2
            and result["original_pre_mcp_failure_preserved"] is True
            and result["source_installation_and_mcp"]["passed"] is True
            and result["source_installation_and_mcp"]["check"]["stages_recorded"] == 5
            and result["wheel_mcp"]["passed"] is True and result["wheel_mcp"]["stages_recorded"] == 5
            and result["model_called"] is False and result["independent_human_replication"] is False,
            "fresh public userland narrative differs from strict replay")
    return {"path": relative, "reader_sha256": digest(Path(reader.__file__)), "reconciliation": result}


def fresh_public_text(evidence, commit):
    if evidence is None:
        return ""
    guide = PUBLIC + "/blob/" + commit + "/research/softwarex/FRESH_LINUX_053_REPRODUCTION.md"
    return (
        "A new Alpine userland and empty Docker store on the shared Windows/WSL host pulled the public image. "
        "The original helper's metadata-limit refusal is retained. A versioned metadata-only correction outside "
        "the anonymous checkout then passed source/wheel checks (ten MCP stages), plus two expected fresh failures. "
        "This \\href{" + guide + "}{synthetic reproduction} is author-run, not independent human or hardware replication.")


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
        abstract = "The four-target study reconciled 168 fresh comparisons and 96 reused successes."
        result = (
            "All 168 requests in 24 completed planned blocks agreed with fresh full-target checks: 96 optimized hits "
            "and 72 non-hits. All 504 arm invocations and 168 fresh-oracle captures are retained. "
            "After a VM crash at 21 complete blocks, the unchanged driver recovered only unfinished preselected "
            "Packaging blocks four through six. Coverage is recovered, not uninterrupted; missing setup totals "
            "exclude Packaging's setup-inclusive ratio. "
            f"The interrupted attempt retains {replication['additional_interrupted_complete_requests']} complete request, "
            f"{replication['additional_incomplete_requests']} partial request and an outcome-less invocation. Including its surviving "
            f"costs gives Packaging's direct/optimized ratio {next(r for r in replication['subjects'] if r['workload'] == 'packaging')['all_recorded_attempt_direct_to_fast_ratio']:.3f}; all attempts remain archived.")
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
    fresh_public = fresh_public_evidence(preview)
    if fresh_public:
        client_text += "\n\n" + fresh_public_text(fresh_public, commit)
    handoff = handoff_evidence(preview)
    actual_agent = actual_agent_evidence(preview)
    reader_audit = consumer_lizard_audit_evidence(actual_agent, handoff, preview)
    if handoff:
        main = handoff["controlled_runs"]["v6_main_corrected"]
        c = main["complete_paired_costs"]
        require(main["all_selected_cases_completed"] and main["complete_case_count"] == 24,
                "final abstract needs full V6 coverage")
        abstract = ("A corrected-fixture study completed 24 selected cases across eight repositories, with "
                    "48 reused successes agreeing with fresh checks. Scripted request latency decreased; total "
                    "cost varied and host timing remains qualified. Fresh checks verified five of six model-produced "
                    "fixes; 15 guided model-consumer turns distinguished prior status, fresh success and fresh failure.")
    slots = {"PUBLIC_COMMIT": commit, "ABSTRACT_RESULT": abstract, "REPLICATION_RESULT": result,
             "REPLICATION_ROWS": table, "STATE_REJOIN": state_text, "ORIGINAL_RESULTS": original_text}
    slots.update({"CLIENT_EVIDENCE": client_text, "OPERATING_REGION": operating_text,
                  "HANDOFF_EVIDENCE": handoff_text(handoff, commit),
                  "AGENT_EVIDENCE": agent_application_text(actual_agent, commit) if not preview else "[Actual client application pending.]"})
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
    summary = {"schema": "zerorun.softwarex-paper.v1", "preview": preview, "public_release": publication, "title": TITLE,
        "template_sha256": digest(source), "main_tex_sha256": hashlib.sha256(document.encode()).hexdigest(),
        "bibliography_sha256": hashlib.sha256(refs.encode()).hexdigest(),
        "original_comparison": original, "engineering": engineering, "fine_grained_revision": revision,
        "replication": replication, "state_rejoin": state_binding, "trace_summary_sha256": digest(EVIDENCE / "trace-summary-v1.json"),
        "inventory": inventory, "extension": extension, "application": application, "handoff": handoff,
        "actual_agent_application": actual_agent,
        "consumer_lizard_audit": reader_audit,
        "fresh_public_userland": fresh_public,
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
