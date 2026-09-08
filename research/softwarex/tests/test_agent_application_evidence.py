"""Internal interpretation review cannot hide or relabel recorded model outcomes."""
import json

import pytest

from research.softwarex import build_agent_application_evidence as evidence


@pytest.fixture
def reviewed(tmp_path):
    stages, rows = [], []
    for stage in ("available", "fresh", "restored"):
        relative = "cases/fixture/plan/attempts/" + stage
        path = tmp_path / evidence.CONSUMERS / relative / "model.stdout.log"
        path.parent.mkdir(parents=True)
        raw = ("synthetic unit-test stream for " + stage).encode()
        path.write_bytes(raw)
        message = "synthetic assessed statement " + stage
        stages.append({"stage": stage, "model_invoked": True, "raw_path": relative,
                       "analysis": {"final_message": message, "material_interpretation_failure": False}})
        rows.append({"case_id": "fixture", "stage": stage, "transcript_sha256": evidence.sha(raw),
                     "final_message_sha256": evidence.sha(message.encode()),
                     "assessment": {"status_accurate": True, "freshness_accurate": True, "limitations_accurate": True},
                     "unverifiable_details": [],
                     "note": "Synthetic review fixture; not an actual application result."})
    campaign = {"record_manifest_sha256": "a" * 64, "cases": [{"case_id": "fixture", "stages": stages}]}
    value = {"schema": "zerorun.real-consumer-internal-review.0.5.3.v1", "rows": rows,
             "record_manifest_sha256": "a" * 64, "independent_human_review": False,
             "method": "Synthetic review-binding test only."}
    path = tmp_path / evidence.REVIEW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return tmp_path, campaign, value, path


def test_exact_review_reconciles_without_claiming_external_review(reviewed):
    root, campaign, _, _ = reviewed
    observed = evidence.review_consumer_messages(root, campaign)
    assert observed["planned_stages"] == observed["supported_interpretations"] == 3
    assert not observed["independent_human_review"]


@pytest.mark.parametrize("change", ["manifest", "transcript", "message", "missing", "order", "external", "hidden-failure"])
def test_review_rejects_staleness_omission_or_adverse_relabel(reviewed, change):
    root, campaign, value, path = reviewed
    if change == "manifest": value["record_manifest_sha256"] = "b" * 64
    elif change == "transcript": value["rows"][0]["transcript_sha256"] = "c" * 64
    elif change == "message": campaign["cases"][0]["stages"][0]["analysis"]["final_message"] += " changed"
    elif change == "missing": value["rows"].pop()
    elif change == "order": value["rows"].reverse()
    elif change == "external": value["independent_human_review"] = True
    else: campaign["cases"][0]["stages"][0]["analysis"]["material_interpretation_failure"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): evidence.review_consumer_messages(root, campaign)


def test_unattempted_stage_remains_in_denominator_without_success(reviewed):
    root, campaign, value, path = reviewed
    campaign["cases"][0]["stages"][2] = {"stage": "restored", "model_invoked": False}
    value["rows"][2].update(transcript_sha256=None, final_message_sha256=None, assessment=None,
                            note="No model invocation in this synthetic fixture.")
    path.write_text(json.dumps(value))
    observed = evidence.review_consumer_messages(root, campaign)
    assert observed["planned_stages"] == 3 and observed["assessed_messages"] == 2
    assert observed["unassessed_stages"] == 1


def test_explicit_adverse_interpretation_is_retained(reviewed):
    root, campaign, value, path = reviewed
    campaign["cases"][0]["stages"][0]["analysis"]["material_interpretation_failure"] = True
    value["rows"][0]["assessment"]["freshness_accurate"] = False
    path.write_text(json.dumps(value))
    observed = evidence.review_consumer_messages(root, campaign)
    assert observed["adverse_interpretations"] == 1 and observed["supported_interpretations"] == 2


def test_unverifiable_detail_is_neither_verified_nor_called_false(reviewed):
    root, campaign, value, path = reviewed
    value["rows"][0]["unverifiable_details"] = ["Wire flag absent from retained client event."]
    path.write_text(json.dumps(value))
    observed = evidence.review_consumer_messages(root, campaign)
    assert observed["adverse_interpretations"] == 0
    assert observed["supported_interpretations"] == 2
    assert observed["supported_core_status_freshness_and_limits"] == 3
    assert observed["messages_with_unverifiable_details"] == 1


@pytest.mark.parametrize("changed", [None, "RECORD_MANIFEST.json", "completion.json"])
def test_producer_tree_must_match_consumer_preparation_pins(tmp_path, monkeypatch, changed):
    from research.softwarex.agent_application_053 import prepare_consumers
    path = tmp_path / evidence.ORACLES
    path.mkdir(parents=True)
    for name, pin in (("RECORD_MANIFEST.json", "ORACLE_MANIFEST"), ("completion.json", "ORACLE_COMPLETION")):
        raw = ("synthetic original " + name).encode()
        monkeypatch.setattr(prepare_consumers, pin, evidence.sha(raw))
        (path / name).write_bytes(raw + (b" substituted tree" if name == changed else b""))
    if changed:
        with pytest.raises(ValueError, match="pinned source"):
            evidence.producer_consumer_binding(tmp_path)
    else:
        assert len(evidence.producer_consumer_binding(tmp_path)) == 2
