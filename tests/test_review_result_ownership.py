"""A later correction cannot certify earlier invented experimental results.

Semantic grades here are explicit adversarial fixtures. These tests verify the
production all-turn contract and versioned instructions, not model accuracy.
"""
from copy import deepcopy
import hashlib

import pytest

from lib.domain.training_surface import training_record_surface_issue
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.domain.workflow_quality import accepted
from lib.prompts import get, render
from lib.prompts.workflow_fact_review import REVIEW_SCORE_V1_8
from lib.prompts.workflow_repair import REVIEW_REPAIR_V1_9


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def grade():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "本轮结论与其实际对象、发生状态和可用证据一致。"}


def conversation(middle=None, *, observed=False):
    instruction = ("我已抽取100条完成标注，其中72条符合标准，请帮我组织结果段。" if observed else
                   "我打算自己抽100条，现在还没评，先帮我写个结果段。")
    return {"messages": [
        {"role": "user", "content": "论文作者抽200条做质量评估，我也想做类似评估。"},
        {"role": "assistant", "content": "可以参考其抽样方法，并为自己的评估记录实际标注结果。",
         "reasoning_content": "论文的样本属于作者的评估，用户的新评估需要独立记录自己的结果。"},
        {"role": "user", "content": instruction},
        {"role": "assistant", "content": middle or "可以写成：在本次抽取的100条指令中，多数指令有意义，实例虽有噪声但多数仍可用于训练。",
         "reasoning_content": "论文作者在200条样本中发现多数指令有意义，所以本次100条评估也可按多数有意义来写。"},
        {"role": "user", "content": "还没做评估，不能把它写成我的真实结果。"},
        {"role": "assistant", "content": "应保留待填写的结果，不能把论文作者的发现当作这100条已经测得的结果。",
         "reasoning_content": "当前没有这100条的观测记录，因此只能说明评估设计或提供待填模板。"},
    ]}


def complete_assessment(candidate):
    return {**grade(), "turn_verdicts": [{**grade(), "message_index": turn["message_index"],
        "content_sha256": turn["content_sha256"], "leakage_free": True, "critical_error": False}
        for turn in supervised_assistant_turns(candidate)]}


def test_unsupported_100_item_result_fails_the_whole_dialogue_even_after_correct_final_turn():
    candidate = conversation()
    raw = complete_assessment(candidate)
    middle = raw["turn_verdicts"][1]
    middle.update(keep=False, grounded=False, reasoning_valid=False, correctness=2,
        critical_error=True, reason="assistant[3]把论文作者200条样本的发现搬成用户尚未开展的100条评估结果；后续纠正不能支持早期断言。")
    middle["scores"].update(correctness=2, grounding=1, reasoning=2)
    frozen = deepcopy(raw)
    result = turn_review_verdict(raw, supervised_assistant_turns(candidate))
    assert not accepted(result) and not result["keep"] and not result["grounded"]
    assert result["scores"]["grounding"] == 1
    assert result["aggregate_original"]["scores"]["grounding"] == 5
    assert "assistant[3]" in result["reason"] and raw == frozen
    assert result["turn_verdicts"][-1]["keep"]  # A correct final reply does not erase the earlier failure.


def test_critical_result_ownership_error_cannot_be_averaged_away_by_high_numeric_scores():
    candidate = conversation()
    raw = complete_assessment(candidate)
    raw["turn_verdicts"][1].update(critical_error=True,
        reason="虽然数值评分被错误给高，本轮仍标明将未实施的用户评估写成已有结果。")
    result = turn_review_verdict(raw, supervised_assistant_turns(candidate))
    assert result["scores"]["correctness"] == 5 and not accepted(result) and not result["keep"]


@pytest.mark.parametrize("answer,reasoning", [
    ("结果段可先保留：本次抽取100条，其中【实际符合标准的条数】条符合标准；主要问题为【实际标注发现】。",
     "评估尚未实施，不能确定合格数量或主要问题，应为实际标注留下明确待填项。"),
    ("若实际评估显示多数指令有意义，可以在结果段说明这一点；当前尚无这100条的观察结果。",
     "条件句将可能的发现与尚未取得的实际结果区分，后续写入论文前仍须由用户的标注支持。"),
    ("论文作者报告其200条样本中多数指令有意义；这不能证明你计划抽取的100条会有同样结果。",
     "两次评估的样本和结果所有者不同，来源结论只属于原作者已完成的评估。"),
])
def test_explicit_placeholder_hypothesis_and_attributed_findings_remain_usable(answer, reasoning):
    candidate = conversation(middle=answer)
    candidate["messages"][3]["reasoning_content"] = reasoning
    assert training_record_surface_issue("multiturn", candidate, protocol_version=2) is None
    assert accepted(turn_review_verdict(complete_assessment(candidate), supervised_assistant_turns(candidate)))


def test_actual_user_observations_change_context_binding_without_a_first_person_ban():
    planned = conversation()
    observed = conversation(middle="本次评估的100条中，72条符合标准，比例为72%。", observed=True)
    observed["messages"][3]["reasoning_content"] = "用户报告已标注100条、其中72条符合标准，72除以100得到72%；这里使用用户报告而非论文样本。"
    old = complete_assessment(planned)
    new_turns = supervised_assistant_turns(observed)
    with pytest.raises(ValueError, match="stale_assistant_turn_review"):
        turn_review_verdict(old, new_turns)
    assert accepted(turn_review_verdict(complete_assessment(observed), new_turns))


def test_new_prompts_bind_experimental_ownership_and_allow_legitimate_writing_help():
    assert "论文对200条抽样的发现，不能搬成用户尚未开展的100条评估的发现" in REVIEW_SCORE_V1_8
    assert "每一被监督轮次当时可见的真实前文" in REVIEW_SCORE_V1_8
    assert "该轮keep=false、grounded=false，整段keep=false" in REVIEW_SCORE_V1_8
    assert "可以写成" in REVIEW_SCORE_V1_8 and "明确假设" in REVIEW_SCORE_V1_8
    assert "真实用户已报告自己的观测数据" in REVIEW_SCORE_V1_8
    assert "不能只凭“我/我们/多数”等关键词判断" in REVIEW_SCORE_V1_8
    assert "逐条修正整段中所有受影响的助手答案及推理，不仅在最后一轮加纠正" in REVIEW_REPAIR_V1_9
    assert "不得继续用错误的结果归属支持答案，也不能清空推理" in REVIEW_REPAIR_V1_9
    assert "明确的假设/虚构授权可正常服务" in REVIEW_REPAIR_V1_9


def test_previous_frozen_review_and_repair_versions_remain_byte_identical():
    score = get("workflow.review_score", "1.7.0")
    repair = get("workflow.review_repair", "1.8.0")
    assert hashlib.sha256(score.template.encode()).hexdigest() == "8c1d204223498257c343115bf90ea594220c9385400c0cb8c6e271af69650875"
    assert hashlib.sha256(repair.template.encode()).hexdigest() == "ec242dbfdb30e6a3a24a80d17845109f43c44f90e08a5a2603aaeab237bd72c6"
    assert REVIEW_SCORE_V1_8.startswith(render(score))
    assert REVIEW_REPAIR_V1_9.startswith(render(repair))
