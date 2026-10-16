"""Native repair stops on support gaps without paid retries or lost bodies."""
from copy import deepcopy
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_native_regeneration import regenerate_native_cot_item
from lib.prompts import get, render
from test_native_same_task_regeneration import selected_parent
from test_workflow_native_prefill import PREFIX, ANSWER, TEXT
from tests.test_blind_claim_workflow import challenge_response, adversarial_grade_response
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # SDK create and review chat are replaced locally; not even a mock probe.
    yield


def pending_native_flow(tmp_path, monkeypatch, *, mode="assumption", severity="minor"):
    workflow, item, client, reviewer, sent, original = selected_parent(tmp_path, monkeypatch)
    workflow.recipe.update(claim_review_protocol_version=2,
        claim_review_adversarial_protocol_version=1, claim_review_condition_contract_version=1,
        claim_review_wire_schema_version=1, claim_review_focus_version=1)
    workflow.recipe["review_repair"]["max_rounds"] = 3
    for prompt_id, version in (("workflow.review_reference", "2.1.0"), ("workflow.review_score", "1.19.0")):
        workflow.recipe["node_prompt_templates"]["review"][prompt_id] = render(get(prompt_id, version))
    ordinary_chat = reviewer.chat
    reviews = []
    def chat(wire, **kwargs):
        data = model_request_data(wire)
        if data.get("claim_review_protocol_version") != 4:
            return ordinary_chat(wire, **kwargs)
        reviewer.calls.append(deepcopy(wire))
        reviewer.usage["calls"] += 1
        raw = (challenge_response(data) if data["phase"] == "claim_challenge"
               else adversarial_grade_response(data))
        if data["phase"] == "claim_adjudication":
            for claim in raw["claim_checks"]:
                claim["basis"]["evidence"] = [{"quote": TEXT, "source_path": ["teacher_evidence"]}]
            first = raw["claim_checks"][0]
            first["severity"] = severity
            if mode == "assumption":
                first["premises"] = [{"origin": "assumption", "use": "task_condition",
                    "quote": "Only newly created entries are readable by this client."}]
            elif mode == "insufficient":
                first["status"] = "insufficient"
            elif mode == "partial":
                raw["coverage"] = "partial"
        reviews.append({"request": deepcopy(data), "raw": deepcopy(raw)})
        return json.dumps(raw, ensure_ascii=False)
    monkeypatch.setattr(reviewer, "chat", chat)
    return workflow, item, client, reviewer, sent, original, reviews


@pytest.mark.parametrize("mode", ["assumption", "insufficient", "partial"])
@pytest.mark.parametrize("severity", ["minor", "material"])
def test_native_pending_gap_keeps_complete_body_and_stops_before_style_or_another_candidate(
        tmp_path, monkeypatch, mode, severity):
    workflow, item, _, reviewer, sent, original, reviews = pending_native_flow(
        tmp_path, monkeypatch, mode=mode, severity=severity)
    frozen_parent = deepcopy(item["record"])
    # Exercise the production native dispatch that used to bypass the guard.
    row, = workflow._review_repair_item(item)
    assert workflow.recipe["review_repair"]["max_rounds"] == 3
    assert len(sent) == 2  # one new rationale and its native answer
    assert len(reviews) == 2  # one inventory and one final adjudication
    assert len(reviewer.calls) == 3  # prefix check plus those two; no style
    assert [x["request"]["phase"] for x in reviews] == ["claim_challenge", "claim_adjudication"]
    assert row["question"] == original["question"]
    assert row["answer"] == ANSWER and row["reasoning"] == [PREFIX]
    assert row["messages"][-1] == {"role": "assistant", "content": ANSWER, "reasoning_content": PREFIX}
    assert row["reasoning_trace_status"] == "current"
    assert row["reasoning_prefill"] and row["prefix_review"]["keep"]
    assert row["answer_response_details"]["reasoning_content"] == "provider-only continuation"
    assert "style_check" not in row and "native_answer_terminal" not in row
    assert row["status"] == "quarantined" and row["reason"] == "condition_support_pending_review"
    receipt = row["review_repair"]
    assert receipt["status"] == "waiting_manual_review"
    assert receipt["reason"] == receipt["stop_reason"] == "condition_support_pending_review"
    assert receipt["attempts"] == 1
    assert receipt["candidate_sha256"] == engine.digest(workflow._review_payload("cot", row))
    assert receipt["config_sha256"] == engine.digest(workflow.recipe["review_repair"])
    assert len(receipt["history"]) == 1
    assert receipt["history"][0]["phase"] == "final"
    assert receipt["history"][0]["status"] == "waiting_manual_review"
    assert "style" not in receipt["history"][0]
    assert item["record"] == frozen_parent
    assert row["native_regeneration_history"][-1]["answer"] == "OLD_BAD_ANSWER_SENTINEL"
    audit = row["claim_review"]
    actual = audit["assessments"][0]["review"]
    assert actual["condition_guard"]["needs_review"]
    assert actual["raw_response"] == reviews[-1]["raw"]
    assert actual["raw_response"]["verdict"]["keep"] is True
    assert set(actual["raw_response"]["verdict"]["scores"].values()) == {5}
    for collection in ("challenge_reviews", "assessments"):
        for entry in audit[collection]:
            checkpoint = engine.read_json(workflow.path / "checkpoints/review" /
                                          (entry["checkpoint"]["checkpoint_sha256"] + ".json"))
            assert checkpoint["data"] == entry["review"]["raw_response"]
            assert checkpoint["sha256"] == entry["checkpoint"]["response_sha256"]
    # Resuming the same immutable item reuses the complete prefix/answer/reviews.
    before = (len(sent), len(reviewer.calls), len(reviews))
    repeated, = workflow._review_repair_item(item)
    assert repeated == row
    assert (len(sent), len(reviewer.calls), len(reviews)) == before


def test_native_legacy_absence_does_not_enable_the_new_pending_route(tmp_path, monkeypatch):
    workflow, item, _, reviewer, sent, _ = selected_parent(tmp_path, monkeypatch)
    workflow.recipe.pop("claim_review_condition_contract_version", None)
    def old_review(target, updated, evidence, key, instruction):
        updated["claim_review"] = {"assessments": [{"review": {"condition_guard": {"needs_review": True}}}]}
        return {"keep": False, "grounded": True, "reasoning_valid": True, "correctness": 3,
                "scores": {key: 3 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "A legacy rejection follows the existing bounded route."}
    monkeypatch.setattr(workflow, "_review_score", old_review)
    row, = regenerate_native_cot_item(workflow, item)
    assert row["review_repair"]["status"] == "rejected"
    assert "stop_reason" not in row["review_repair"]
    assert row["reason"] == "native_regeneration_failed_after_review"
    assert "style" in row["review_repair"]["history"][-1]
    assert len(sent) == 2 and len(reviewer.calls) == 2  # prefix and style


def test_native_supported_condition_review_still_runs_style_and_accepts(tmp_path, monkeypatch):
    workflow, item, _, reviewer, sent, _, reviews = pending_native_flow(tmp_path, monkeypatch, mode="pass")
    row, = workflow._review_repair_item(item)
    assert row["status"] == "eligible" and row["review_repair"]["status"] == "accepted"
    assert "stop_reason" not in row["review_repair"]
    assert row["style_check"]["keep"]
    assert "style" in row["review_repair"]["history"][-1]
    assert len(sent) == 2 and len(reviews) == 2 and len(reviewer.calls) == 4
    assert not row["claim_review"]["assessments"][0]["review"]["condition_guard"]["needs_review"]
