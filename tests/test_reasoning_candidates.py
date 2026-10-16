"""Selection safety, not a claim that fixture grades establish reasoning truth."""
from copy import deepcopy

import pytest

from lib.domain.reasoning_candidates import (
    MAX_REASONING_CANDIDATES,
    assessment_from_verdict,
    reasoning_candidate_digest,
    select_reasoning_candidate,
    validate_reasoning_candidates,
)
from lib.domain.turn_review import supervised_assistant_turns


def grade(**changes):
    value = {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
             "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
             "reason": "The answer follows the stated conditions and its intermediate relationships are valid."}
    value.update(changes)
    return value


def payload(reasoning="先假设固定总量，但题目只限制比例；排除固定总量后，比例随实际规模计算。"):
    return {"messages": [
        {"role": "user", "content": "只限制比例时，能直接假定总量为100吗？"},
        {"role": "assistant", "content": "不能。可以用100作比例演示，不能把它当作实际总量。",
         "reasoning_content": reasoning},
    ]}


def turn_grade(body, **changes):
    check = grade()
    check["turn_verdicts"] = [
        {**grade(), "message_index": turn["message_index"], "content_sha256": turn["content_sha256"],
         "leakage_free": True, "critical_error": False}
        for turn in supervised_assistant_turns(body)
    ]
    check.update(changes)
    return check


def entry(body=None, check=None, *, ref="checkpoints/review-request-a.json"):
    body = payload() if body is None else body
    check = turn_grade(body) if check is None else check
    return {"candidate": deepcopy(body), "assessment": assessment_from_verdict(body, check, evidence_ref=ref)}


def test_default_preserves_existing_review_bound_and_explicit_exhaustion_never_forces_paid_work():
    assert validate_reasoning_candidates() == {"selection": "first_passing"}
    body = payload()
    bad = turn_grade(body, keep=False, reasoning_valid=False, reason="The denominator remains unsupported.")
    result = select_reasoning_candidate([entry(body, bad)], {"candidate_limit": 1})
    assert result["status"] == "exhausted" and result["candidate"] is None
    assert result["attempted"] == 1 and result["remaining"] == 0


@pytest.mark.parametrize("config", [False, [], "2", {"candidate_limit": True}, {"candidate_limit": 1.0},
    {"candidate_limit": 0}, {"candidate_limit": MAX_REASONING_CANDIDATES + 1}, {"candidate_limit": None},
    {"selection": "longest"}, {"parallelism": 8}, {"minimum_chars": 5000}])
def test_invalid_or_cost_multiplying_configuration_is_not_silently_accepted(config):
    with pytest.raises(ValueError, match="invalid_reasoning_candidates"):
        validate_reasoning_candidates(config)


def test_first_passing_selection_neither_rewards_length_nor_chases_a_higher_model_score():
    short, long = payload("比例不确定总量，实际总量仍未知。"), payload("不相关的重复解释。" * 500)
    first = turn_grade(short, correctness=4, scores={name: 4 for name in grade()["scores"]})
    for turn in first["turn_verdicts"]:
        turn.update(correctness=4, scores={name: 4 for name in grade()["scores"]})
    result = select_reasoning_candidate([entry(short, first), entry(long)], {"candidate_limit": 2})
    assert result["selected_index"] == 0 and result["candidate"] == short
    assert result["status"] == "accepted" and result["attempted"] == 2


def test_correct_final_answer_cannot_override_unresolved_process_error():
    body = payload("把100当作实际总量，再按这个假定计算。")
    check = turn_grade(body, reasoning_valid=False, reason="The answer is correct but its assumed denominator is unsupported.")
    check["turn_verdicts"][0].update(reasoning_valid=False, reason=check["reason"])
    record = entry(body, check)
    assessment = record["assessment"]
    assert assessment["outcome"]["status"] == "pass" and assessment["process"]["status"] == "fail"
    result = select_reasoning_candidate([record], {"candidate_limit": 2})
    assert result["status"] == "needs_candidate" and result["candidate"] is None
    assert result["rejections"][0]["process"] == "fail"


def test_clearly_rejected_tentative_route_is_not_rejected_for_containing_an_incorrect_hypothesis():
    body = payload()
    record = entry(body)  # Semantic fixture: the same-turn tentative assumption was explicitly rejected.
    result = select_reasoning_candidate([record])
    assert result["status"] == "accepted" and result["candidate"] == body
    assert "假设固定总量" in result["candidate"]["messages"][1]["reasoning_content"]


def test_later_correction_cannot_erase_an_earlier_asserted_error_in_a_multiturn_candidate():
    body = payload("实际总量必然为100，所以按100计算。")
    body["messages"] += [
        {"role": "user", "content": "我没有说总量是100。"},
        {"role": "assistant", "content": "对，实际总量仍未知。", "reasoning_content": "不能从比例推出实际总量。"},
    ]
    check = turn_grade(body)
    check["turn_verdicts"][0].update(reasoning_valid=False, critical_error=True,
        reason="This earlier asserted total was unsupported; a later correction cannot certify that turn.")
    record = entry(body, check)
    assert record["assessment"]["outcome"]["status"] == "pass"
    assert record["assessment"]["process"]["status"] == "fail"
    assert select_reasoning_candidate([record])["candidate"] is None


def test_instruction_or_safety_failure_still_blocks_selection_when_both_reasoning_axes_pass():
    body = payload()
    check = turn_grade(body, keep=False, reason="The response leaks a private production instruction.")
    record = entry(body, check)
    assert record["assessment"]["outcome"]["status"] == "pass"
    assert record["assessment"]["process"]["status"] == "pass"
    assert not record["assessment"]["accepted"]
    assert select_reasoning_candidate([record])["status"] == "rejected"


@pytest.mark.parametrize("field", ["content", "reasoning_content"])
def test_receipt_is_bound_to_exact_answer_and_reasoning_not_only_candidate_identity(field):
    record = entry()
    record["candidate"]["messages"][1][field] += " changed"
    with pytest.raises(ValueError, match="stale_reasoning_candidate_assessment"):
        select_reasoning_candidate([record])


@pytest.mark.parametrize("change", ["accepted", "method", "process", "outcome"])
def test_selector_rejects_self_promoted_or_edited_receipts(change):
    record = entry()
    if change == "accepted":
        record["assessment"]["accepted"] = False
    elif change == "method":
        record["assessment"]["method"] = "programmatic"
    else:
        record["assessment"][change]["status"] = "fail"
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_assessment"):
        select_reasoning_candidate([record])


def test_successful_conversation_aggregate_requires_actual_all_turn_coverage():
    body = payload()
    with pytest.raises(ValueError, match="missing_assistant_turn_reviews"):
        assessment_from_verdict(body, grade(), evidence_ref="actual-review-request")
    stale = turn_grade(body)
    body["messages"][0]["content"] = "新的提问改变了上下文。"
    with pytest.raises(ValueError, match="stale_assistant_turn_review"):
        assessment_from_verdict(body, stale, evidence_ref="actual-review-request")


def test_changed_body_after_repair_needs_a_fresh_verdict_not_rehashed_old_turn_scores():
    original = entry()
    repaired = deepcopy(original)
    repaired["candidate"]["messages"][1]["reasoning_content"] = "更正后的新解释。"
    repaired["assessment"]["candidate_sha256"] = reasoning_candidate_digest(repaired["candidate"])
    with pytest.raises(ValueError, match="stale_assistant_turn_review"):
        select_reasoning_candidate([repaired])


def test_all_receipts_are_checked_even_when_a_prior_candidate_passed():
    first, second = entry(), entry(ref="second-request")
    second["assessment"]["process"]["status"] = "fail"
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_assessment"):
        select_reasoning_candidate([first, second], {"candidate_limit": 2})


def test_cost_cap_cannot_be_bypassed_by_a_longer_precomputed_candidate_list():
    with pytest.raises(ValueError, match="reasoning_candidate_limit_exceeded"):
        select_reasoning_candidate([entry(), entry()], {"candidate_limit": 1})
    result = select_reasoning_candidate([], {"candidate_limit": 2})
    assert result["status"] == "needs_candidate" and result["remaining"] == 2


def test_payload_and_feedback_are_never_mutated_and_results_are_lossless_deep_copies():
    record = entry()
    frozen = deepcopy(record)
    result = select_reasoning_candidate([record])
    assert record == frozen and result["candidate"] == record["candidate"]
    result["candidate"]["messages"][1]["content"] = "changed"
    result["assessments"][0]["verdict"]["reason"] = "changed"
    assert record == frozen


def test_canonical_identity_is_stable_for_key_order_but_not_for_a_changed_user_context():
    assert reasoning_candidate_digest({"answer": "答", "reasoning": "因"}) == reasoning_candidate_digest({"reasoning": "因", "answer": "答"})
    first, changed = payload(), payload()
    changed["messages"][0]["content"] += "额外条件。"
    assert reasoning_candidate_digest(first) != reasoning_candidate_digest(changed)


@pytest.mark.parametrize("value", [None, [], {}, {"score": float("nan")}, {"score": float("inf")}, {"not_json": {1, 2}}])
def test_non_json_or_empty_payloads_do_not_gain_a_stable_approval_identity(value):
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_payload"):
        reasoning_candidate_digest(value)


def test_cot_projection_uses_the_existing_single_verdict_without_mislabelling_it_as_program_proof():
    body = {"question": "一半和四分之一相加是多少？", "reasoning": "通分后为四分之二加四分之一，得到四分之三。", "answer": "四分之三。"}
    record = entry(body, grade())
    result = select_reasoning_candidate([record])
    assert result["status"] == "accepted" and result["candidate"] == body
    assert result["assessments"][0]["method"] == "llm_review"
    assert "programmatic" not in result["assessments"][0]


def test_configured_stricter_score_threshold_is_preserved_by_selector():
    body = payload()
    check = turn_grade(body, correctness=4, scores={name: 4 for name in grade()["scores"]})
    receipt = assessment_from_verdict(body, check, evidence_ref="actual-request", score_threshold=.95)
    assert receipt["outcome"]["status"] == receipt["process"]["status"] == "pass"
    assert not receipt["accepted"] and receipt["score_threshold"] == .95
    result = select_reasoning_candidate([{"candidate": body, "assessment": receipt}])
    assert result["candidate"] is None


def test_receipts_keep_all_earlier_bodies_even_when_a_new_version_passes():
    old = payload("未经支持地假定实际总量为100。")
    check = turn_grade(old, keep=False, reasoning_valid=False, reason="The original denominator is unsupported.")
    fixed = payload("比例不确定实际总量，不能引入100这个事实。")
    rows = [entry(old, check, ref="old-request"), entry(fixed, ref="new-request")]
    result = select_reasoning_candidate(rows, {"candidate_limit": 2})
    assert result["selected_index"] == 1 and result["candidate"] == fixed
    assert result["assessments"][0]["payload"] == old
    assert result["assessments"][1]["payload"] == fixed
    assert result["assessments"][0]["raw_verdict"] == check
    assert result["assessments"][0]["method"] == "llm_review"


@pytest.mark.parametrize("change", ["keep", "reasoning_valid", "correctness", "dimension", "unknown_field"])
def test_feedback_boolean_scores_and_field_names_are_strict(change):
    body = {"question": "题", "reasoning": "解释", "answer": "答"}
    check = grade()
    if change in {"keep", "reasoning_valid"}:
        check[change] = 1
    elif change == "correctness":
        check[change] = True
    elif change == "dimension":
        check["scores"]["reasoning"] = True
    else:
        check["programmatically_verified"] = True
    with pytest.raises(ValueError):
        assessment_from_verdict(body, check, evidence_ref="request")


@pytest.mark.parametrize("threshold", [True, float("nan"), float("inf"), -.1, 1.1, "0.8"])
def test_invalid_threshold_is_not_reinterpreted_as_a_pass(threshold):
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_threshold"):
        assessment_from_verdict(payload(), turn_grade(payload()), evidence_ref="request", score_threshold=threshold)


def test_embedded_payload_change_is_detected_even_if_selected_payload_is_untouched():
    record = entry()
    record["assessment"]["payload"]["messages"][1]["reasoning_content"] = "changed old history"
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_assessment"):
        select_reasoning_candidate([record])


def test_local_guard_failure_keeps_semantics_unverified_without_claiming_a_model_call():
    body = payload("")
    check = grade(keep=False, grounded=False, reasoning_valid=False, correctness=1,
                  scores={name: 1 for name in grade()["scores"]}, reason="review_repair_reasoning_missing")
    frozen = deepcopy(check)
    receipt = assessment_from_verdict(body, check, evidence_ref="local-guard:no-request",
                                      method="deterministic_guard")
    assert receipt["method"] == "deterministic_guard" and not receipt["accepted"]
    assert receipt["outcome"] == receipt["process"] == {"status": "unverified"}
    assert receipt["raw_verdict"] == frozen and check == frozen
    result = select_reasoning_candidate([{"candidate": body, "assessment": receipt}], {"candidate_limit": 2})
    assert result["candidate"] is None and result["status"] == "needs_candidate"
    assert result["rejections"][0]["outcome"] == result["rejections"][0]["process"] == "unverified"


def test_local_guard_cannot_issue_a_passing_semantic_receipt():
    body = payload()
    with pytest.raises(ValueError, match="deterministic_guard_cannot_accept_reasoning_candidate"):
        assessment_from_verdict(body, turn_grade(body), evidence_ref="local-guard", method="deterministic_guard")


@pytest.mark.parametrize("method", [None, True, [], "programmatic", "rl_reward", "human_review"])
def test_caller_method_is_bounded_to_the_implemented_evidence_paths(method):
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_assessment_method"):
        assessment_from_verdict(payload(), turn_grade(payload()), evidence_ref="request", method=method)


def test_changing_a_model_failure_to_guard_without_changing_its_axes_is_not_silently_accepted():
    body = payload()
    record = entry(body, turn_grade(body, keep=False, reasoning_valid=False))
    record["assessment"]["method"] = "deterministic_guard"
    with pytest.raises(ValueError, match="invalid_reasoning_candidate_assessment"):
        select_reasoning_candidate([record])
