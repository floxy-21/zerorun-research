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
    template += "\n@@PUBLIC_COMMIT@@ @@REPLICATION_RESULT@@ @@REPLICATION_ROWS@@ @@STATE_REJOIN@@ @@ORIGINAL_RESULTS@@ @@CLIENT_EVIDENCE@@ @@OPERATING_REGION@@ @@HANDOFF_EVIDENCE@@"
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
