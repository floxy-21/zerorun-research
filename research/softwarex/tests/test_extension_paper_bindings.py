"""Reject stale or overstated extension prose without executing model calls."""
from copy import deepcopy

import pytest

from research.softwarex import build_paper as paper
from research.softwarex import build_extension_evidence as extension


def example():
    return {
        "scripted_client_conformance": {
            "scripted_cases": 14, "scripted_passed": 14, "installed_stdio_requests": 8,
            "live_checks": 11, "live_checks_passed": 11,
        },
        "bounded_live_client": {
            "recorded": True, "functional_lifecycle_pass": True,
            "agent_stages_recorded": 4, "independently_validated_stages": [{}, {}, {}, {}],
        },
        "operating_region": {
            "constructed_hit_fraction": 4 / 7,
            "subjects": [{"workload": name, "crossover": {
                "regime": "reuse_faster_above_equality", "equality_hit_fraction": p},
                "hit_cost_attribution": {"fractions_of_outer": {"two_fingerprints": 0.74}}}
                for name, p in zip(("click", "pycparser", "colorama", "packaging"), (0.54, 0.64, 0.56, 0.55))],
        },
    }


def test_passing_client_has_bounded_exact_meaning():
    client, costs = paper.extension_text(example())
    assert "four constrained Codex" in client and "not agent understanding" in client
    assert "14 response cases" in client and "eight actual stdio requests" in client
    assert "post-hoc" in costs and "unmeasured, not zero" in costs


def test_adverse_client_is_retained_not_recast_as_success():
    value = example()
    value["bounded_live_client"].update(functional_lifecycle_pass=False, agent_stages_recorded=2)
    client, _ = paper.extension_text(value)
    assert "did not complete successfully; 2 agent turns" in client
    assert "completed four" not in client


def test_missing_preview_remains_explicit():
    value = example()
    value["bounded_live_client"].update(recorded=False, functional_lifecycle_pass=False)
    assert "Layout preview" in paper.extension_text(value)[0]


def test_actual_refusal_description_does_not_become_success():
    value = example()
    value["bounded_live_client"].update(functional_lifecycle_pass=False, agent_stages_recorded=1,
        stage_adverse_outcomes=[{"raw_diagnostics": {
            "classification": "untrusted_doctor_readiness_refusal", "mcp_calls": 1,
            "run_tests_calls": 0, "exact_marker_only": False}}])
    text = paper.extension_text(value)[0]
    assert "no matching manifest authority" in text and "no retry was made" in text
    assert "completed four" not in text


def test_separate_nonmodel_correction_never_replaces_original_failure():
    value = example()
    value["bounded_live_client"].update(functional_lifecycle_pass=False, agent_stages_recorded=1)
    value["explicit_trust_path_diagnostic"] = {
        "recorded": True, "passed": True, "negative_control_passed": True,
        "stages_recorded": 5, "stages_validated": 5, "model_called": False,
        "codex_correction_tested": False,
    }
    text = paper.extension_text(value)[0]
    assert "did not complete successfully" in text
    assert "not a corrected Codex run" in text
    value["explicit_trust_path_diagnostic"]["model_called"] = True
    with pytest.raises(ValueError, match="configuration diagnostic"):
        paper.extension_text(value)


@pytest.mark.parametrize("mutation", ["scripted", "live_checks", "turns", "validated", "subjects", "direction", "fraction"])
def test_narrative_invariants_fail_closed(mutation):
    value = example()
    if mutation == "scripted":
        value["scripted_client_conformance"]["scripted_passed"] = 13
    elif mutation == "live_checks":
        value["scripted_client_conformance"]["live_checks_passed"] = 10
    elif mutation == "turns":
        value["bounded_live_client"]["agent_stages_recorded"] = 3
    elif mutation == "validated":
        value["bounded_live_client"]["independently_validated_stages"].pop()
    elif mutation == "subjects":
        value["operating_region"]["subjects"].reverse()
    elif mutation == "direction":
        value["operating_region"]["subjects"][0]["crossover"]["regime"] = "never"
    else:
        value["operating_region"]["constructed_hit_fraction"] = 0.8
    with pytest.raises(ValueError):
        paper.extension_text(value)


def test_final_requires_saved_exact_reconciliation(monkeypatch):
    value = {"completed": True, "preview": False, "bounded": "observed"}
    monkeypatch.setattr(extension, "build", lambda *args, **kwargs: deepcopy(value))
    monkeypatch.setattr(paper, "load", lambda *_: deepcopy(value))
    assert paper.extension_evidence() == value
    monkeypatch.setattr(paper, "load", lambda *_: {**value, "bounded": "changed"})
    with pytest.raises(ValueError, match="stale"):
        paper.extension_evidence()


@pytest.mark.parametrize("field", ["completed", "preview"])
def test_incomplete_extension_cannot_enter_final(monkeypatch, field):
    value = {"completed": True, "preview": False}
    value[field] = not value[field]
    monkeypatch.setattr(extension, "build", lambda *args, **kwargs: value)
    with pytest.raises(ValueError, match="completed extension"):
        paper.extension_evidence()
