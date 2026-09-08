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
        return "[Layout preview: reconciled issue-state and agent handoff evidence pending.]"
    acquisition = evidence["acquisition"]
    pilot_repositories = len({row["repo"] for row in acquisition["cases"] if row["phase"] == "pilot"})
    paragraphs = [(
        "A separate feasibility study uses public issue-state repositories with supplied regression tests. "
        f"The frozen shortlist yielded {acquisition['selected_main_cases']} main cases across "
        f"{acquisition['selected_main_repositories']} repositories and {acquisition['selected_pilot_cases']} pilot cases "
        f"across {pilot_repositories} {'repository' if pilot_repositories == 1 else 'repositories'}; "
        f"the original acquisition target was {acquisition['planned_main_cases']} main cases across "
        f"{acquisition['planned_main_repositories']} repositories. Selection used a fixed repository shortlist, metadata "
        "validity and hash ordering, not measured outcomes. Selected, unavailable and unexecuted cases remain in the ledger. "
        "In controlled reference-patch handoffs, both arms validate the same final source: a cold producer followed "
        "by a consumer, either fresh twice or ZeroRun seed then reuse. Separate fresh oracles check the results; "
        "a diagnostics request is measured separately. These are imposed handoffs, not observed repetition frequencies.")]
    labels = {"v1_pilot": "Copy pilot", "v1_main": "Copy main",
              "v2_pilot": "Image pilot", "v2_pilot_repeat": "Image repeat", "v2_main": "Image main",
              "v2_main_extended": "Image extended", "v2_fresh_pilot": "Image rebuild",
              "v3_main_clean": "Clean image main", "v4_main_full": "Full image main"}
    interrupted_image = evidence["controlled_runs"].get("v2_pilot", {}).get("timing_context", {}).get("interruption_reported") is True
    if interrupted_image:
        labels["v2_pilot"] = "Image recovered$^{*}$"
    interrupted_extended = evidence["controlled_runs"].get("v2_main_extended", {}).get("timing_context", {}).get("interruption_reported") is True
    if interrupted_extended:
        labels["v2_main_extended"] = "Main repeat$^{*}$"
    clean_main = evidence["controlled_runs"].get("v3_main_clean", {})
    clean_timing_qualified = clean_main.get("timing_context", {}).get("sampled_continuity_checks_passed") is True
    if clean_main.get("state") == "RECONCILED_RECORDED_OUTCOMES" and not clean_timing_qualified:
        labels["v3_main_clean"] = "New main$^{*}$"
    full_main = evidence["controlled_runs"].get("v4_main_full", {})
    full_timing_qualified = full_main.get("timing_context", {}).get("sampled_continuity_checks_passed") is True
    if full_main.get("state") == "RECONCILED_RECORDED_OUTCOMES" and not full_timing_qualified:
        labels["v4_main_full"] = "Full main$^{*}$"
    table_rows, outcome_notes = [], []
    table_runs = dict(evidence["controlled_runs"])
    rebuilt = evidence.get("fresh_public_source_reproduction", {})
    if rebuilt.get("pilot", {}).get("state") == "RECONCILED_RECORDED_OUTCOMES":
        table_runs["v2_fresh_pilot"] = rebuilt["pilot"]
    for name, run in table_runs.items():
        if run["state"] != "RECONCILED_RECORDED_OUTCOMES":
            continue
        require(name in labels, "unrecognized handoff cohort requires an explicit manuscript label")
        result = run["reconciliation"]
        if run["version"] == "v2":
            result = result["controlled_handoffs"]
        count = result["complete_cases"]
        if count:
            times = result["complete_paired_chain_ms"]
            change = 100 * (times["zerorun"] / times["fresh"] - 1)
            direction = "slower" if change >= 0 else "faster"
            costs = run["complete_paired_costs"]
            blocks = costs["complete_blocks"]
            require(blocks == result["complete_blocks"] and costs["chain_ms"] == times,
                    "handoff narrative cost denominators differ")
            consumer, setup = costs["consumer_ms"], costs["setup_inclusive_chain_ms"]
            table_rows.append(
                f"{labels[name]} & {count}/{result['selected_cases']} & {blocks}/{costs['cache_hits']} & "
                f"{times['fresh']/1000:.2f}/{times['zerorun']/1000:.2f} & "
                f"{consumer['fresh']/blocks/1000:.2f}/{consumer['zerorun']/blocks/1000:.2f} & "
                f"{setup['fresh']/1000:.2f}/{setup['zerorun']/1000:.2f}" + r" \\")
            # The table retains every measured total. Do not turn small observed
            # differences or interrupted timings into standalone speedup claims.
        else:
            table_rows.append(f"{labels[name]} & 0/{result['selected_cases']} & 0/0 & --- & --- & ---" + r" \\")
        if result["material_correctness_stop"] or (result["failed_operations"] and name not in {"v2_main_extended", "v3_main_clean", "v4_main_full"}):
            outcome_notes.append(f"{labels[name]} retains {result['failed_operations']} failed operations; "
                                 f"a material-correctness stop was {'recorded' if result['material_correctness_stop'] else 'not recorded'}.")
        if run["campaign_error"]:
            outcome_notes.append(f"{labels[name]} ended with a retained campaign error.")
    if table_rows:
        paragraphs.append("\n".join([
            r"\begin{table}[htbp]\centering\small",
            r"\begin{tabular}{@{}lrrrrr@{}}\toprule",
            r"Deployment & Cases & Blocks/hits & Chain (s) & Consumer (s) & With setup (s) \\",
            r"\midrule", *table_rows, r"\bottomrule\end{tabular}",
            r"\caption{Fresh/ZeroRun costs, without pooling cohorts. Cases are completed/selected; chain and setup-inclusive times sum complete blocks; consumer times are request means. Setup excludes common image construction/acquisition. Dashes denote missing timings. $^{*}$Recovered image pilot and Main repeat were interrupted by host disk exhaustion/VM pauses. Guest-clock operation durations omit the host pause and are descriptive audit values, not uninterrupted performance measurements. Any other starred run lacks qualifying continuity observations.}",
            r"\label{tab:handoffs}\end{table}"]))
        if outcome_notes:
            paragraphs.append(" ".join(outcome_notes))
        repeat = evidence["controlled_runs"].get("v2_pilot_repeat")
        if repeat and repeat["state"] == "RECONCILED_RECORDED_OUTCOMES":
            costs = repeat["complete_paired_costs"]
            saving = 100 * (1 - costs["consumer_ms"]["zerorun"] / costs["consumer_ms"]["fresh"])
            overhead = 100 * (costs["chain_ms"]["zerorun"] / costs["chain_ms"]["fresh"] - 1)
            paragraphs.append(
                f"Consumer waiting, chain time and provenance are distinct objectives: the image repeat reduced "
                f"consumer latency by {saving:.1f}\\% while increasing chain cost by {overhead:.1f}\\%. "
                "A downstream reviewer needing only the identified earlier status can benefit from a shorter wait "
                "after producer work has finished; one needing new diagnostics must execute fresh. The interface "
                "makes this choice explicit and auditable. These imposed requests demonstrate that tradeoff, "
                "not observed user preferences, productivity or autonomous consumer behavior.")
    main = evidence["controlled_runs"].get("v2_main")
    if main and main["state"] == "RECONCILED_RECORDED_OUTCOMES":
        protocol = load(ROOT / main["path"] / "run/protocol.json")
        dispositions = [row["disposition"] for row in main["cases"]]
        require(protocol["budget_seconds"] == 300 and protocol["execution_seconds"] == 120
                and [row["case_id"] for row in protocol["selection"]["cases"]]
                == [row["case_id"] for row in main["cases"]], "original main stopping ledger differs")
        require(dispositions[:4] == ["COMPLETE", "COMPLETE", "INCOMPLETE_OR_UNSUPPORTED", "INCOMPLETE_OR_UNSUPPORTED"]
                and dispositions[4:] == ["NOT_RUN_BUDGET"] * 20, "original main dispositions differ")
        paragraphs.append("The original 300-second main completed two pycparser cases; compatibility and "
                          "budget limits prevented further pairs, leaving 20 unrun. It establishes bounded feasibility only.")
    extended = evidence["controlled_runs"].get("v2_main_extended", {})
    if extended.get("state") == "RECONCILED_RECORDED_OUTCOMES":
        cases = extended["cases"]
        completed = [row for row in cases if row["disposition"] == "COMPLETE"]
        unsupported = sum(row["disposition"] == "INCOMPLETE_OR_UNSUPPORTED" for row in cases)
        unrun = sum(row["disposition"] == "NOT_RUN_BUDGET" for row in cases)
        repositories = len({row["repo"] for row in completed})
        paragraphs.append(
            f"The interrupted repeat attempted all {len(cases)} cases in frozen order: "
            f"{len(completed)} completed across {repositories} repositories, {unsupported} were incomplete/unsupported, "
            f"and {unrun} were unrun. The nine incomplete cases stopped before paired handoffs: runtime/test "
            "compatibility, unusable collection, cleanup uncertainty, repaired-state assertion failure or empty "
            "captures. These case dispositions are distinct from failed-operation counts and incorrect reuses. "
            "The coverage audit retains every stopping reason and unresolved cause.")
    image = evidence["image_build"]
    if image["state"] == "RECONCILED_RECORDED_IMAGE":
        preparation = image["reconciliation"]
        paragraphs.append(
            f"Image preparation took {preparation['setup_outer_ms']/1000:.2f}~s outside chain ratios, placing frozen "
            "dependencies in the container instead of each source copy. The image is locally digest-bound; "
            "public pulling and independent bit-identical rebuilding are not established.")
    else:
        paragraphs.append("No completed dependency-image preparation is established in the available records.")
    if (rebuilt.get("pilot", {}).get("state") == "RECONCILED_RECORDED_OUTCOMES"
            and rebuilt.get("fresh_real_workload_reproduction_confirmed") is True):
        fresh = rebuilt["pilot"]
        paragraphs.append(
            f"An author-side public-source recipe rebuild took {rebuilt['image']['setup_outer_ms']/1000:.2f}~s "
            f"and supported {fresh['complete_case_count']}/{fresh['selected_case_count']} original pilot cases "
            "with fresh oracles (Image rebuild). Existing Docker layers returned the historical digest; "
            "this was not an uncached or independent-laboratory rebuild.")
    clean_rebuild = evidence.get("clean_public_source_reproduction", {})
    if clean_rebuild.get("fresh_image_main_reproduction_confirmed") is True:
        clean = clean_rebuild["main"]
        complete = [row for row in clean["cases"] if row["disposition"] == "COMPLETE"]
        incomplete = sum(row["disposition"] == "INCOMPLETE_OR_UNSUPPORTED" for row in clean["cases"])
        unrun = sum(row["disposition"] == "NOT_RUN_BUDGET" for row in clean["cases"])
        paragraphs.append(
            f"A subsequent public-source build used Docker \\code{{--no-cache}} "
            f"({clean_rebuild['image']['setup_outer_ms']/1000:.2f}~s), binding the actual new image identity "
            f"to the same ordered cohort. Under the 1,200-second budget, {len(complete)} cases completed across "
            f"{len({row['repo'] for row in complete})} repositories, {incomplete} were incomplete/unsupported "
            f"and {unrun} unrun. Every completed handoff had a fresh oracle. Existing VM/base layers and "
            "dependency caches were allowed; clean-OS and independent-human replication are not established.")
        if clean_timing_qualified:
            paragraphs.append("Host/guest samples bracketed the command with no observed interruption; "
                              "shorter pauses cannot be excluded. Monitoring is part of the measured environment.")
        else:
            paragraphs.append("Host observations do not qualify this rerun's timing as uninterrupted; "
                              "the retained qualification reasons constrain interpretation to feasibility.")
    if full_main.get("state") == "RECONCILED_RECORDED_OUTCOMES":
        full_cases = full_main["cases"]
        complete = sum(row["disposition"] == "COMPLETE" for row in full_cases)
        incomplete = sum(row["disposition"] == "INCOMPLETE_OR_UNSUPPORTED" for row in full_cases)
        attempted = sum(not row["disposition"].startswith("NOT_RUN")
                        and row["disposition"] != "UNAVAILABLE_ACQUISITION" for row in full_cases)
        paragraphs.append(
            f"A subsequent full-cohort rerun attempted {attempted}/{len(full_cases)} selected cases in the original "
            f"order using that same rebuilt image: {complete} completed and {incomplete} were incomplete/unsupported. "
            "The 20-minute campaign cutoff was removed; 120-second command safeguards remained. This is a "
            "separate repeat, not additional independent subjects or a new image rebuild.")
        if full_timing_qualified:
            paragraphs.append("Host/guest samples cover the full command with no observed interruption. "
                              "Small cost differences are descriptive, not statistically established acceleration.")
        else:
            paragraphs.append("Its host observations do not qualify uninterrupted timing; retained durations "
                              "therefore support no uninterrupted performance claim.")
    if evidence["earlier_image_preparation"]["state"] == "RECONCILED_RECORDED_PREPARATION_FAILURE":
        failed_setup = evidence["earlier_image_preparation"]["setup_outer_ms"]
        paragraphs.append(f"The earlier failed image preparation ({failed_setup/1000:.2f}~s) is retained.")
    paragraphs.append("Acquisition and operator review are outside paired totals, not assigned zero; "
                      "fresh-oracle and diagnostics costs remain separate.")
    observed_phases = []
    for phase, agent in evidence["agent_evaluation"].items():
        if not agent["recorded_model_invocations"]:
            continue
        observed_phases.append(phase)
        producers = [row["reconciliation"] for row in agent["producer_cases"] if "reconciliation" in row]
        models = sorted({row["requested_model"] for row in producers if row.get("requested_model")})
        model_text = ", ".join(models).replace("_", r"\_") if models else "the recorded model configuration"
        invocations = agent["recorded_model_invocations"]
        text = (f"The coding-agent producer {phase} requested {model_text}: "
                f"{invocations} model {'invocation' if invocations == 1 else 'invocations'} "
                f"among {agent['selected_cases']} selected cases. The public issue and regression tests "
                "were supplied; the reference source solution was withheld. Transcripts, snapshots and failures are retained. ")
        if any(row.get("pre_invocation_error") == "ValueError: isolated CODEX_HOME must not contain user config"
               for row in agent["producer_cases"]):
            text += ("Another case stopped before model invocation at client-isolation preflight; "
                     "it remains selected, not a failed model patch. ")
        evaluation = agent["evaluation"]
        if evaluation["state"] == "RECONCILED_RECORDED_AGENT_EVALUATION":
            result = evaluation["reconciliation"]
            fixes = result["completed_verified_fixes"]
            text += (f"Separate fresh execution verified {fixes} completed {'fix' if fixes == 1 else 'fixes'} "
                     f"among {result['selected_cases']} selected cases. A verified fix requires previously failing "
                     "regression nodes to pass with the same collected node set and a source patch within the recorded boundary. "
                     f"The downstream scripted handoff completed {result['paired_blocks']} paired blocks; "
                     "its timing excludes model production and does not measure an autonomous consumer decision.")
            costs = evaluation["complete_paired_costs"]
            if costs["complete_blocks"]:
                chain, consumer, setup = (costs[key] for key in
                                          ("chain_ms", "consumer_ms", "setup_inclusive_chain_ms"))
                text += (f" Summed fresh/ZeroRun costs were {chain['fresh']/1000:.2f}/{chain['zerorun']/1000:.2f}~s "
                         f"for the chain, {consumer['fresh']/1000:.2f}/{consumer['zerorun']/1000:.2f}~s for its consumers, "
                         f"and {setup['fresh']/1000:.2f}/{setup['zerorun']/1000:.2f}~s including per-arm setup, "
                         "under the same exclusions as Table~\\ref{tab:handoffs}.")
            if any(row["case_id"] == "tobymao__sqlglot-3425"
                   and row["classification"]["completed_verified_fix"] is True for row in result["cases"]):
                text += (" The verified SQLGlot example concerns a MySQL \\code{REPLACE INTO} parsing failure: "
                         "the production patch preserves the statement as a command, and the original failing "
                         "regression passes fresh. This verifies the supplied target, not full SQL semantic support.")
        else:
            text += "Independent final-patch verification is incomplete, so no resolved issue is claimed."
        paragraphs.append(text)
        if (evaluation["state"] == "RECONCILED_RECORDED_AGENT_EVALUATION"
                and any(row["case_id"] == "tobymao__sqlglot-3425"
                        and row["classification"]["completed_verified_fix"] is True
                        for row in evaluation["reconciliation"]["cases"])):
            paragraphs.append(qualification_example(agent, commit))
    if not observed_phases:
        paragraphs.append("No genuine coding-agent producer is established in the available records.")
    return "\n\n".join(paragraphs)


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
        "A scripted consumer passed 14 response cases; 11 installed-server checks covered eight discovery/diagnostic requests. "
        "Three Codex~0.153.3 configurations failed overall: missing authority, approval refusal, and an unsupported "
        "argument after four passing lifecycle turns and two agreeing fresh checks. A separate API-guided demonstration, "
        "after non-model setup, passed two model decisions (reuse for earlier status; verification for fresh evidence), "
        "two fresh oracles and six no-tool interpretations including failure and refusal. These eight turns had no retries. "
        "The supplement's complete trial ledger retains failures and transcripts; "
        "the guided result is not a causal documentation experiment. "
        "A researcher-executed public-clone guide passed refusal, readiness, execution, reuse and verification "
        "through the installed server with 36 matching runtime modules. "
        f"The recorded installation took {installation['elapsed_seconds']:.1f}~s and the server check {quickstart['elapsed_seconds']:.1f}~s, "
        "excluding cloning, Docker setup and researcher preparation; no independent user observation is claimed.")


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
            "A VM crash after 21 complete blocks required an explicit recovery of only unfinished preselected "
            "Packaging blocks four through six, using the unchanged driver. The supplement retains failures and "
            "recovery records; coverage is recovered, not uninterrupted. Packaging's unflushed setup total is "
            "unavailable and its setup-inclusive ratio omitted. "
            f"The interrupted attempt retains {replication['additional_interrupted_complete_requests']} complete request, "
            f"{replication['additional_incomplete_requests']} partial request and an outcome-less invocation. Including its surviving "
            f"arm costs changes Packaging's direct/optimized ratio to {next(r for r in replication['subjects'] if r['workload'] == 'packaging')['all_recorded_attempt_direct_to_fast_ratio']:.3f}.")
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
    handoff = handoff_evidence(preview)
    if handoff:
        repeat = handoff["controlled_runs"]["v2_pilot_repeat"]["complete_paired_costs"]
        extended_main = handoff["controlled_runs"].get("v2_main_extended", {})
        clean_main = handoff["controlled_runs"].get("v3_main_clean", {})
        full_main = handoff["controlled_runs"].get("v4_main_full", {})
        main = (full_main if full_main.get("complete_case_count", 0) else
                clean_main if clean_main.get("complete_case_count", 0) else
                extended_main if extended_main.get("complete_case_count", 0) else handoff["controlled_runs"]["v2_main"])
        consumer_saving = 100 * (1 - repeat["consumer_ms"]["zerorun"] / repeat["consumer_ms"]["fresh"])
        chain_overhead = 100 * (repeat["chain_ms"]["zerorun"] / repeat["chain_ms"]["fresh"] - 1)
        main_saving = 100 * main["complete_paired_costs"]["chain_saved_fraction"]
        direction = "lower" if main_saving >= 0 else "higher"
        repositories = len({row["repo"] for row in main["cases"] if row["disposition"] == "COMPLETE"})
        abstract += (f" An image pilot repeat reduced consumer latency {consumer_saving:.1f}\\% but increased "
                     f"producer-plus-consumer time {chain_overhead:.1f}\\%.")
        if (main.get("timing_context", {}).get("interruption_reported") is True
                or (main is clean_main or main is full_main)
                and not main.get("timing_context", {}).get("sampled_continuity_checks_passed")):
            abstract += (f" A feasibility repeat completed {main['complete_case_count']}/"
                         f"{main['selected_case_count']} selected cases across {repositories} repositories; "
                         "its timings do not establish uninterrupted acceleration.")
        else:
            abstract += (f" A bounded main study completed {main['complete_case_count']}/"
                         f"{main['selected_case_count']} selected cases across {repositories} repositories, "
                         f"with {abs(main_saving):.1f}\\% {direction} chain time.")
    slots = {"PUBLIC_COMMIT": commit, "ABSTRACT_RESULT": abstract, "REPLICATION_RESULT": result,
             "REPLICATION_ROWS": table, "STATE_REJOIN": state_text, "ORIGINAL_RESULTS": original_text}
    slots.update({"CLIENT_EVIDENCE": client_text, "OPERATING_REGION": operating_text,
                  "HANDOFF_EVIDENCE": handoff_text(handoff, commit)})
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
