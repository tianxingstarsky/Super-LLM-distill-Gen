"""AI preference evidence and reward-model dataset projection."""
from __future__ import annotations

from copy import deepcopy

import pytest

from lib.domain.workflow_targets import (rlaif_feedback_issue, rlaif_reward_model_record,
                                         training_record)


def pair():
    def check(score, reason):
        return {"keep": score >= 4, "grounded": score >= 4, "reasoning_valid": score >= 4,
                "correctness": score, "scores": {"correctness": score, "reasoning": score,
                "grounding": score, "instruction": score, "safety": score}, "reason": reason}

    return {"prompt": [{"role": "user", "content": "2+2=?"}],
            "chosen": [{"role": "assistant", "content": "4"}],
            "rejected": [{"role": "assistant", "content": "5"}],
            "preference": {"dimension": "correctness", "chosen": check(5, "Arithmetic is correct."),
                           "rejected": check(1, "Arithmetic is wrong."), "minimum_gap": 2},
            "rlaif": {"criterion": "correctness", "judge": "jev",
                      "chosen_feedback": "Arithmetic is correct.",
                      "rejected_feedback": "Arithmetic is wrong."}}


def test_rlaif_scored_feedback_and_reward_model_projection_share_verified_label():
    source = pair()
    original = deepcopy(source)
    assert rlaif_feedback_issue(source) is None
    native = training_record("rlaif", source)
    reward = rlaif_reward_model_record(source)
    assert native["responses"][0]["preference_rank"] == 1
    assert native["responses"][1]["preference_rank"] == 2
    assert [item["score"] for item in native["responses"]] == [5, 1]
    assert reward == {key: source[key] for key in ("prompt", "chosen", "rejected")}
    assert source == original


@pytest.mark.parametrize("change,reason", [
    (lambda row: row["preference"]["chosen"]["scores"].update(correctness=4),
     "rlaif_correctness_score_mismatch"),
    (lambda row: row["rlaif"].update(chosen_feedback="Different assessment"),
     "rlaif_feedback_reason_mismatch"),
    (lambda row: row["rlaif"].update(criterion="safety"), "rlaif_criterion_mismatch"),
    (lambda row: row["preference"].update(minimum_gap=5), "rlaif_preference_gap_mismatch"),
    (lambda row: row["preference"]["chosen"].update(keep=False), "rlaif_chosen_not_accepted"),
])
def test_rlaif_rejects_unsubstantiated_labels(change, reason):
    source = pair()
    change(source)
    assert rlaif_feedback_issue(source) == reason
    with pytest.raises(ValueError, match=reason):
        rlaif_reward_model_record(source)
