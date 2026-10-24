"""Scoped repair, gradual drift, and real failures with exact prior audits."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from lib.contexts.review.domain.claim_repair_feedback import claim_repair_feedback
from lib.contexts.review.domain.preference_candidates import validate_contrast_assessment, contrastive_relationship_issue
from lib.contexts.review.domain.reviewed_failure import (find_reviewed_failure, validate_reviewed_failure_pair,
    remember_failed_positive, reuse_failed_positive)
from lib.contexts.review.application.assess_preference_pair import AssessPreferencePair
from lib.contexts.review.infrastructure.preference_style import preference_repair_requirements
from lib.contexts.review.infrastructure.review_style import current_claim_review_requirements
from lib.contexts.workflows.application.generate_preference import GeneratePreferencePair
from lib.contexts.workflows.contracts import new_preference_design
from lib.domain.reasoning_candidates import assessment_from_verdict
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.prompts import get, render
from tests.test_claim_review_windows import WindowClient, long_sample
from tests.test_condition_review_workflow import flow, evidence
from tests.test_contrastive_preference import pair, BlindPort
from tests.test_reasoning_depth_review import pure_worker, sample, controlled
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class QualityGapClient(WindowClient):
    def __init__(self, *, content_failure=False):
        super().__init__()
        self.content_failure = content_failure

    def chat(self, messages, **kwargs):
        request = model_request_data(messages)
        raw = json.loads(super().chat(messages, **kwargs))
        if request.get("phase") == "claim_adjudication":
            raw["verdict"]["keep"] = False
            raw["verdict"]["scores"]["instruction"] = 3
            raw["verdict"]["reason"] = "Key-phrase translation and a concrete boundary check are missing."
            if self.content_failure:
                raw["verdict"].update(reasoning_valid=False, correctness=2, grounded=False)
                raw["verdict"]["scores"].update(correctness=2, grounding=2, reasoning=2)
                raw["claim_checks"][0].update(status="refuted", severity="material")
        return json.dumps(raw)


def failed_sample(tmp_path, *, content_failure=True, depth_control=False):
    runtime, row = flow(tmp_path, QualityGapClient(content_failure=content_failure)), long_sample()
    if depth_control:
        row = controlled(row)
    verdict = runtime._review_claim_score("sft", row, evidence(), ["reviewed-failure"])
    payload = runtime._review_payload("sft", row)
    assessment = assessment_from_verdict(payload, verdict, evidence_ref="actual-test-review-checkpoints",
        method="llm_claim_review", claim_review=row["claim_review"])
    return runtime, row, payload, assessment


def test_style_only_window_failure_is_delivered_even_with_no_disputed_facts(tmp_path):
    runtime, row, payload, _ = failed_sample(tmp_path, content_failure=False)
    audit = deepcopy(row["claim_review"])
    legacy = claim_repair_feedback(payload, evidence(), audit)
    current = claim_repair_feedback(payload, evidence(), audit, include_quality_feedback=True)
    assert not legacy["disputes"] and "quality_feedback" not in legacy
    assert current["quality_feedback"] and not current["disputes"]
    for item in current["quality_feedback"]:
        assert item["verdict"]["scores"]["instruction"] == 3
        assert item["unit_ids"] and "translation" in item["verdict"]["reason"]
    assert row["claim_review"] == audit


def test_repair_resolves_the_exact_same_positive_style_as_the_anonymous_reviewer():
    runtime, authored = pure_worker(), controlled(sample())
    record = {"id": authored["id"], "prompt": authored["messages"][:-1], "chosen": authored["messages"][-1:],
        "preference_response_origins": {"chosen": authored["reasoning_origin"]}}
    expected = current_claim_review_requirements(runtime, "sft", authored)
    actual = preference_repair_requirements(runtime, record)
    assert actual["chosen"] == expected
    assert actual["chosen"]["generation_style"]["reasoning_depth"] == "deep"
    assert "generation_style" not in actual["rejected"]
    record["preference_response_origins"]["chosen"] = "provider_reasoning"
    assert "generation_style" not in preference_repair_requirements(runtime, record)["chosen"]


def test_drift_is_semantically_linked_and_quotes_both_user_anchor_and_answer_turn():
    record = pair()
    record["rejected"][0].update(content="重复记录会显得单调。既然如此，重要的是让界面看起来有变化，可以先给每行换颜色。",
        reasoning_content="重复意味着单调，增加颜色就能让记录不同。")
    value = {"same_task": False, "opposite": False, "off_topic": True, "topic_linked": True,
        "associative_drift": True, "topic_anchor": "重复记录", "drift_quote": "重要的是让界面看起来有变化",
        "chosen_claim": "先判断，再写出。", "rejected_claim": "可以先给每行换颜色。",
        "rationale_supports_answer": True, "rationale_quote": "重复意味着单调", "reason": "从去重错误联想到视觉单调，改成配色而未解决去重。"}
    checked = validate_contrast_assessment(value, record, version=3)
    assert contrastive_relationship_issue(checked, new_preference_design()) is None
    unrelated = {**value, "topic_linked": False, "associative_drift": False, "topic_anchor": "", "drift_quote": ""}
    assert contrastive_relationship_issue(validate_contrast_assessment(unrelated, record, version=3),
        new_preference_design()) == "preference_associative_drift_required"
    for key in ("topic_anchor", "drift_quote"):
        with pytest.raises(ValueError, match="preference_drift_quote_not_in"):
            validate_contrast_assessment({**value, key: "Invented quotation"}, record, version=3)


def test_complete_review_failure_is_reused_without_a_generation_call_or_reasoning_truncation(tmp_path):
    _, old, payload, assessment = failed_sample(tmp_path)
    updated = deepcopy(old)
    updated["messages"][-1]["content"] = "Corrected task response."
    updated["messages"][-1]["reasoning_content"] = "Full corrected reasoning."
    updated.update(status="eligible", source_id="source1", evidence_level="source_and_model_assessed",
        reasoning_distillation={"assessments": [assessment]})
    port = Mock()
    output, = GeneratePreferencePair(port, {"preference_design_version": 3,
        "preference_design": new_preference_design()}).execute(updated)
    port.request.assert_not_called()
    assert output["prompt"] == updated["messages"][:-1]
    assert output["rejected"] == payload["messages"][-1:]
    assert len(output["rejected"][0]["reasoning_content"]) > 160
    assert validate_reviewed_failure_pair(output)
    for field in ("content", "reasoning_content"):
        changed = deepcopy(output); changed["rejected"][0][field] += "changed"
        with pytest.raises(ValueError, match="reviewed_failure_body_or_task_changed"):
            validate_reviewed_failure_pair(changed)


def test_style_failure_alone_and_different_task_cannot_be_recycled_as_task_and_reasoning_failures(tmp_path):
    _, row, _, assessment = failed_sample(tmp_path, content_failure=False)
    row["reasoning_distillation"] = {"assessments": [assessment]}
    row["messages"][-1]["content"] = "Corrected."
    assert find_reviewed_failure(row) is None
    _, row, _, assessment = failed_sample(tmp_path / "other")
    row["reasoning_distillation"] = {"assessments": [assessment]}
    row["messages"][0]["content"] += " A different user task."
    assert find_reviewed_failure(row) is None
    row["reasoning_distillation"]["assessments"][0]["candidate_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale_reasoning_candidate_assessment"):
        find_reviewed_failure(row)


def test_new_templates_pin_separately_and_custom_prompts_remain_literal():
    current = snapshot_node_prompts(None, recipe_version=22, preference_design_version=3, preference_design_prompt_version=1)
    prior = snapshot_node_prompts(None, recipe_version=22, preference_design_version=2, preference_design_prompt_version=1)
    assert current["review"]["workflow.review_repair"] == render(get("workflow.review_repair", "1.16.0"))
    assert prior["review"]["workflow.review_repair"] == render(get("workflow.review_repair", "1.15.0"))
    assert prior["preference"]["workflow.preference_design_check"] == render(get("workflow.preference_design_check", "1.0.0"))
    custom = {"review": {"workflow.review_repair": "My exact {custom} revision prompt."}}
    assert snapshot_node_prompts(custom, recipe_version=22, preference_design_version=3)["review"] == {
        **current["review"], **custom["review"]}


def test_failed_positive_is_retained_then_reused_only_after_content_changes(tmp_path):
    _, old, payload, assessment = failed_sample(tmp_path)
    record = {"id": old["id"], "source_id": "paper", "prompt": payload["messages"][:-1],
        "chosen": payload["messages"][-1:], "rejected": pair()["rejected"]}
    response = {"claim_review": old["claim_review"], "verdict": assessment["derived_verdict"]}
    remember_failed_positive(record, response, evidence(), threshold=.8)
    remember_failed_positive(record, response, evidence(), threshold=.8)
    assert len(record["preference_review_failures"]) == 1
    assert not reuse_failed_positive(record)
    record["chosen"] = [{"role": "assistant", "content": "Corrected task answer.",
        "reasoning_content": "Full corrected reasoning with the actual prerequisites."}]
    assert reuse_failed_positive(record)
    assert record["rejected"] == payload["messages"][-1:]
    assert validate_reviewed_failure_pair(record)
    config = {"preference_design_version": 3, "preference_design": new_preference_design(),
        "review_repair": {"mode": "auto", "max_rounds": 1, "score_threshold": .8}}
    accepted, verdict, stop = AssessPreferencePair(BlindPort(scores=(5, 1)), config).execute(record, evidence())
    assert verdict["keep"] and not stop
    assert accepted["preference_assessment"]["negative_source"] == "reviewed_failure"
    assert "contrast" not in accepted["preference_assessment"]
    # Full real failures need not be short, but still need the original score gap.
    _, verdict, _ = AssessPreferencePair(BlindPort(scores=(5, 4)), config).execute(record, evidence())
    assert not verdict["keep"]
    port = BlindPort(scores=(5, 1))
    base = port.assess_response
    def fresh_pending(*args, **kwargs):
        result = base(*args, **kwargs)
        result["condition_pending"] = len(port.calls) == 2
        return result
    port.assess_response = fresh_pending
    _, verdict, stop = AssessPreferencePair(port, config).execute(record, evidence())
    assert not verdict["keep"] and stop == "condition_support_pending_review"


def test_runtime_repair_sends_scoped_style_and_complete_quality_feedback(tmp_path):
    runtime, old, payload, assessment = failed_sample(tmp_path, content_failure=False, depth_control=True)
    runtime.recipe.update(preference_design_version=3, preference_design=new_preference_design(),
        node_generation={"sft": {"enabled": True, "style": "concise", "reasoning_depth": "deep"}})
    record = {"id": old["id"], "source_id": "paper", "prompt": payload["messages"][:-1],
        "chosen": payload["messages"][-1:], "rejected": pair()["rejected"],
        "preference_response_origins": {"chosen": "prompt_styled_generation"},
        "preference_assessment": {"responses": [{"claim_review": old["claim_review"]}]}}
    runtime.ask = Mock(return_value={"uncertain": True})
    _, issue = runtime._repair_content("dpo", record, evidence(), ["scoped-repair"], assessment["verdict"], "")
    assert issue == "review_repair_content_unavailable"
    submitted = runtime.ask.call_args.args[3]
    assert submitted["response_requirements"] == preference_repair_requirements(runtime, record)
    assert submitted["claim_repair_feedback"]["scope"] == "chosen"
    assert submitted["claim_repair_feedback"]["quality_feedback"]
    assert not submitted["claim_repair_feedback"]["disputes"]
