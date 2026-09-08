"""Fail-closed manuscript state bindings, without executing experiments."""
from copy import deepcopy
import json

import pytest

from research.softwarex import build_paper as paper


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def binding(tmp_path, monkeypatch):
    here, evidence = tmp_path / "softwarex", tmp_path / "evidence"
    (here / "paper").mkdir(parents=True)
    (here / "generated").mkdir()
    evidence.mkdir()
    monkeypatch.setattr(paper, "HERE", here)
    monkeypatch.setattr(paper, "EVIDENCE", evidence)
    monkeypatch.setattr(paper, "original_analysis", lambda *_: {"rows": [{"workload": "more-itertools",
        "warm_snapshot_latency_reduction_percent": 69.2, "request_speedup": 2.09,
        "whole_task_exit_agreements": 14, "optimized_hit_requests": 8, "totals_ms": {"fast": 7, "snapshot": 6}}]})
    monkeypatch.setattr(paper, "engineering_evidence", lambda: {"windows": {"passed": 1593, "skipped": 55, "subtests passed": 19},
        "integration": {"counts": {"failure": 0, "pass": 99, "safe_refusal": 1}}})
    monkeypatch.setattr(paper, "revision_analysis", lambda: {"rows": [{"comparisons_pass": 200, "reused_nodes": 0}], "all_product_gates_pass": False})
    trace = {"main": {"selected_episode_rows": 128, "valid_episode_rows": 122,
        "valid_distinct_repositories": 104, "valid_distinct_issues": 120, "repeat_pairs": 120, "no_intervening_barrier_pair_count": 0}}
    monkeypatch.setattr(paper, "trace_analysis", lambda: trace)
    write(evidence / "trace-summary-v1.json", trace)
    monkeypatch.setattr(paper, "inventory_check", lambda: {})
    monkeypatch.setattr(paper, "bibliography", lambda _: "")
    monkeypatch.setattr(paper, "extension_evidence", lambda *_: {"fixture_extension": True})
    monkeypatch.setattr(paper, "extension_text", lambda _: ("Bounded client fixture.", "Conditional cost fixture."))
    monkeypatch.setattr(paper, "application_evidence", lambda *_: {"fixture_application": True})
    monkeypatch.setattr(paper, "application_text", lambda _: "Bounded application fixture.")
    analysis = {"completed": True, "requests": 4, "optimized_hits": 1, "source_sha256": "a" * 64}
    monkeypatch.setattr(paper, "state_analysis", lambda *args, **kwargs: deepcopy(analysis))
    text_path = here / "paper/state-rejoin.tex"
    text_path.write_text("A bounded illustrative case, not autonomous-agent replay.", encoding="utf-8")
    analysis_path = evidence / "state-rejoin-analysis-v1.json"
    write(analysis_path, analysis)
    review_path = here / "generated/state-rejoin-review.json"
    write(review_path, {"verified": True, "text_sha256": paper.digest(text_path), "analysis_sha256": paper.digest(analysis_path)})
    template = "\\begin{abstract}@@ABSTRACT_RESULT@@\\end{abstract}\n"
    template += "\n".join("\\section{" + s + "}" for s in ("Motivation and significance", "Software description", "Illustrative examples", "Impact", "Conclusions"))
    template += "\n@@PUBLIC_COMMIT@@ @@REPLICATION_RESULT@@ @@REPLICATION_ROWS@@ @@STATE_REJOIN@@ @@ORIGINAL_RESULTS@@ @@CLIENT_EVIDENCE@@ @@OPERATING_REGION@@ @@HANDOFF_EVIDENCE@@ @@AGENT_EVIDENCE@@"
    (here / "paper/submission.tex.in").write_text(template, encoding="utf-8")
    return here, evidence, analysis, review_path, text_path, analysis_path


def test_matching_state_chain_included_and_bound_even_in_preview(binding):
    *_, text_path, analysis_path = binding
    document, _, evidence = paper.build(preview=True)
    assert text_path.read_text() in document
    assert evidence["state_rejoin"]["analysis_sha256"] == paper.digest(analysis_path)
    assert evidence["state_rejoin"]["independent_analysis"]["requests"] == 4
    assert evidence["preview"]


@pytest.mark.parametrize("mode,match", [
    ("missing-review", "unreviewed"),
    ("changed-text", "review mismatch"),
    ("unverified-review", "review mismatch"),
    ("stale-analysis", "incomplete or stale"),
    ("stale-review-analysis", "analysis binding differs"),
    ("incomplete-analysis", "incomplete or stale"),
    ("wrong-denominator", "denominator changed"),
])
def test_stale_or_unverified_state_never_enters_manuscript(binding, mode, match):
    _, _, analysis, review_path, text_path, analysis_path = binding
    if mode == "missing-review":
        review_path.unlink()
    elif mode == "changed-text":
        text_path.write_text("Changed state claim", encoding="utf-8")
    elif mode in ("unverified-review", "stale-review-analysis"):
        review = paper.load(review_path)
        review["verified" if mode == "unverified-review" else "analysis_sha256"] = False if mode == "unverified-review" else "0" * 64
        write(review_path, review)
    elif mode == "stale-analysis":
        write(analysis_path, {**analysis, "source_sha256": "b" * 64})
    elif mode == "incomplete-analysis":
        analysis["completed"] = False
        write(analysis_path, analysis)
    else:
        analysis["requests"] = 3
        write(analysis_path, analysis)
        review = paper.load(review_path)
        review["analysis_sha256"] = paper.digest(analysis_path)
        write(review_path, review)
    with pytest.raises(ValueError, match=match):
        paper.build(preview=True)


def test_final_manuscript_requires_verified_public_release(binding):
    with pytest.raises(ValueError, match="verified public release required"):
        paper.build(preview=False)


@pytest.fixture
def actual_cases():
    """Artificial semantic fixtures; no model or recorded experiment is run."""
    from research.softwarex.agent_application_053.validation import CASES, STAGES
    producers, consumers, additive = [], [], []
    for index, case_id in enumerate(CASES):
        fixed = index != 2
        producers.append({"case_id": case_id, "classification": {
            "completed_verified_fix": fixed, "baseline_nonxfail_failed_calls": ["synthetic::failed"],
            "baseline_available": True, "final_available": True, "same_collected_node_set": True,
            "baseline_exit_code": 1, "final_exit_code": 0 if fixed else 1}})
        if fixed:
            stages = [{"stage": stage, "model_invoked": True, "process_completed_successfully": True,
                "analysis": {"model_turn_completed": True, "boundary_pass": True,
                    "reuse_observed": stage == "available",
                    "completed_mcp_results": [{"classification": "FRESH_SUCCESS"}] if stage == "fresh" else []}}
                for stage in STAGES]
            consumers.append({"case_id": case_id, "stages": stages})
            additive.append({"case_id": case_id, "stage": "restored", "additional_fresh_failures": 1})
    return {"producer": {"cases": producers}, "consumer": {"cases": consumers},
            "additive_client_event_audit": {"rows": additive}}


def test_actual_case_table_retains_primary_failure_and_guided_consumer_scope(actual_cases):
    text = paper.agent_case_table(actual_cases)
    assert "sqlglot-3182 & 1 & 1 & Ineligible" in text
    assert text.count(" & H / V / F") == 5
    assert "separately assisted repair is excluded" in text


@pytest.mark.parametrize("mutation", ["omitted-case", "duplicate-case", "promoted-failure", "baseline-pass",
                                     "different-node-set", "uncompleted-model", "missing-fresh-failure"])
def test_actual_case_table_rejects_outcome_or_scope_relabel(actual_cases, mutation):
    rows = actual_cases["producer"]["cases"]
    if mutation == "omitted-case": rows.pop()
    elif mutation == "duplicate-case": rows[0]["case_id"] = rows[1]["case_id"]
    elif mutation == "promoted-failure": rows[2]["classification"]["completed_verified_fix"] = True
    elif mutation == "baseline-pass": rows[0]["classification"]["baseline_exit_code"] = 0
    elif mutation == "different-node-set": rows[0]["classification"]["same_collected_node_set"] = False
    elif mutation == "uncompleted-model": actual_cases["consumer"]["cases"][0]["stages"][1]["model_invoked"] = False
    else: actual_cases["additive_client_event_audit"]["rows"][0]["additional_fresh_failures"] = 0
    with pytest.raises(ValueError):
        paper.agent_case_table(actual_cases)


@pytest.mark.parametrize("mutation", [None, "stale-json", "stale-reader-table", "unmatched-claim"])
def test_new_reader_audit_requires_current_source_bound_claims(tmp_path, monkeypatch, mutation):
    from research.softwarex import build_consumer_lizard_audit as audit
    monkeypatch.setattr(paper, "ROOT", tmp_path)
    value = {"summary": {"consumer_rows": 15, "literal_freshness_matches": 15,
                         "literal_outcome_matches": 15, "messages_with_unverifiable_wire_assertions": 13}}
    if mutation == "unmatched-claim": value["summary"]["literal_outcome_matches"] = 14
    actual_application = {"synthetic_reconciled": True}
    handoff = {"controlled_runs": {key: {"reconciliation": {"synthetic_version": version}}
               for version, key in ((5, "v5_main_compatible"), (6, "v6_main_corrected"))}}
    def build(root, *, application, recovered):
        assert root == tmp_path and application is actual_application
        assert recovered == {5: {"synthetic_version": 5}, 6: {"synthetic_version": 6}}
        return value
    monkeypatch.setattr(audit, "build", build)
    monkeypatch.setattr(audit, "markdown", lambda _: "Synthetic reader audit.\n")
    write(tmp_path / audit.JSON_PATH, value if mutation != "stale-json" else {"stale": True})
    path = tmp_path / audit.MARKDOWN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"Synthetic reader audit.\n" if mutation != "stale-reader-table" else b"Altered prose.\n")
    if mutation:
        with pytest.raises(ValueError):
            paper.consumer_lizard_audit_evidence(actual_application, handoff)
    else:
        result = paper.consumer_lizard_audit_evidence(actual_application, handoff)
        assert result["summary"]["consumer_rows"] == 15 and result["independent_human_review"] is False


@pytest.mark.parametrize("mutation", [None, "cached-store", "missing-failure", "partial-source",
                                     "partial-wheel", "lost-original", "human-relabel"])
def test_fresh_userland_prose_requires_full_strict_replay(tmp_path, monkeypatch, mutation):
    from research.softwarex import verify_fresh_public_053 as reader
    monkeypatch.setattr(paper, "ROOT", tmp_path)
    value = {"passed": True, "new_docker_empty_inventory": True, "normal_mcp_requests": 10,
             "fresh_failure_requests": 2, "original_pre_mcp_failure_preserved": True,
             "source_installation_and_mcp": {"passed": True, "check": {"stages_recorded": 5}},
             "wheel_mcp": {"passed": True, "stages_recorded": 5},
             "model_called": False, "independent_human_replication": False}
    if mutation == "cached-store": value["new_docker_empty_inventory"] = False
    elif mutation == "missing-failure": value["fresh_failure_requests"] = 1
    elif mutation == "partial-source": value["source_installation_and_mcp"]["check"]["stages_recorded"] = 4
    elif mutation == "partial-wheel": value["wheel_mcp"]["stages_recorded"] = 4
    elif mutation == "lost-original": value["original_pre_mcp_failure_preserved"] = False
    elif mutation == "human-relabel": value["independent_human_replication"] = True
    def verify(root, directory):
        assert root == tmp_path and directory == tmp_path / "research/softwarex/evidence/public-fresh-linux-053-v1"
        return value
    monkeypatch.setattr(reader, "verify", verify)
    if mutation:
        with pytest.raises(ValueError, match="fresh public userland narrative"):
            paper.fresh_public_evidence()
    else:
        result = paper.fresh_public_evidence()
        assert result["reconciliation"] is value and result["reader_sha256"] == paper.digest(paper.Path(reader.__file__))
        text = paper.fresh_public_text(result, "f" * 40)
        assert "metadata-limit refusal" in text and "outside the anonymous checkout" in text
        assert "not independent human or hardware replication" in text
