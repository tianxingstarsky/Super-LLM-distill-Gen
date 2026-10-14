"""Fact assessment and private editing audits retain separate meanings."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_review_repair import _authored_review_rules, _AUTHORED_REVIEW_RULES
from lib.prompts import get, render
from lib.prompts.workflow_agent_contracts import RATIONALE_CHECK_V1_6
from lib.prompts.workflow_prefill import PREFIX_CHECK_BODY_V1_3


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


METHOD = "编辑反馈提醒避免擅定用户所指。"
QUESTION = "又不行了"
GOOD_REASONING = "这句话没有说明所指对象，当前不足以确认具体情形，应先了解用户想表达什么。"
BAD_REASONING = "这句话只能说明某次操作失败，用户卡在具体步骤，当前应该询问错误步骤。"


def trace():
    return {"task": "回应未明确所指的困惑。", "premises": ["实际用户消息是‘又不行了’。", METHOD],
            "steps": [{"basis": "当前输入未给出对象。", "inference": "缺少对象无法确认具体领域与经历。",
                       "result": "先确认用户所指，不断言发生技术故障。"}],
            "checks": [{"check": "对照实际用户文字核对是否指定操作或步骤。", "result": "原输入没有操作或步骤信息。"}],
            "limitations": ["所指对象尚未知。"]}


class ReviewModel:
    """Offline verdicts check the exact parser and payload; they are not eval scores."""
    model = "offline-audit-boundary-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        assert all(METHOD not in message["content"] for message in messages if message["role"] == "system")
        data = json.loads(messages[-1]["content"])
        candidate = data.get("candidate", data)
        body = candidate.get("reasoning", data.get("reasoning", ""))
        if isinstance(body, list):
            body = "\n".join(body)
        audit = data.get("reasoning_trace", candidate.get("reasoning_trace"))
        assert audit["premises"][-1] == METHOD
        assert METHOD not in body
        valid = "只能说明某次操作失败" not in body
        reason = "编辑方法不证明业务事实；实际文字没有确定对象。" if valid else "正文无依据把未明确所指收窄为操作失败和具体步骤。"
        if "adherence" in messages[0]["content"]:
            return json.dumps({"keep": valid, "adherence": 5 if valid else 2, "reason": reason})
        value = 5 if valid else 2
        return json.dumps({"keep": valid, "grounded": valid, "reasoning_valid": valid, "correctness": value,
            "scores": {key: value for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": reason})


@pytest.mark.parametrize("bad", [False, True])
def test_prefix_and_final_review_parse_real_trace_separately_from_reasoning_and_keep_role_boundaries(tmp_path, bad):
    output = tmp_path / "out"
    run_id = engine.create_run(output, brief="普通开放交流。", targets=["cot"],
        node_generation={"cot": {"enabled": True, "style": "default", "answer_mode": "native_prefill"}})
    model = ReviewModel()
    workflow = engine.Workflow(output, run_id, tmp_path, generator=model, jev=model)
    workflow.stage = "cot"
    candidate = {"reasoning": BAD_REASONING if bad else GOOD_REASONING, "reasoning_trace": trace()}
    data = {"prompt": [{"role": "user", "content": QUESTION}], "candidate": candidate,
            "source": {"kind": "brief", "task": "普通开放交流，不确定具体领域。"}}
    prefix = workflow.adherence_check(["prefix-audit"], "workflow.cot_prefix_check", data)
    final = workflow.ask(["final-audit"], "jev", "workflow.rationale_check",
        {**data, **candidate, "answer": "你说的是哪件事？"}, allow_reasoning_fallback=False)
    assert prefix["keep"] is not bad and final["keep"] is not bad
    assert len(model.calls) == 2
    for messages in model.calls:
        assert messages[0]["role"] == "system"
        assert "编辑" in messages[0]["content"] and "方法" in messages[0]["content"]
        actual = json.loads(messages[-1]["content"])
        assert actual["prompt"] == [{"role": "user", "content": QUESTION}]
        assert actual["candidate"]["reasoning_trace"] == candidate["reasoning_trace"]


@pytest.mark.parametrize("prompt_id,version,body", [
    ("workflow.cot_prefix_check", "1.3.0", PREFIX_CHECK_BODY_V1_3),
    ("workflow.rationale_check", "1.6.0", RATIONALE_CHECK_V1_6),
])
def test_current_full_review_body_is_registered(prompt_id, version, body):
    assert get(prompt_id).version == version
    assert render(get(prompt_id)) == body


@pytest.mark.parametrize("prompt_id,version,expected", [
    ("workflow.cot_prefix_check", "1.2.0", "0c001e82584da766ce0fd58e988b8b6ddea2528867074b0233999526a006235a"),
    ("workflow.rationale_check", "1.5.0", "aa46c3c5434ba4577faed94ffeebe7f3ea6ed2068c7e5cfd0d64ee97fe8b8ee8"),
])
def test_old_review_template_is_byte_identical(prompt_id, version, expected):
    assert sha256(get(prompt_id, version=version).template.encode()).hexdigest() == expected


@pytest.mark.parametrize("projection,version,new_rules", [(2, "1.6.0", False), (3, "1.5.0", False), (3, "1.6.0", True)])
def test_package_review_extra_rules_use_new_boundary_only_for_opted_in_runs(projection, version, new_rules):
    recipe = {"authored_role_projection_version": projection,
              "prompts": {"workflow.rationale_check": {"version": version}}}
    rules = _authored_review_rules(recipe)
    if new_rules:
        assert rules == RATIONALE_CHECK_V1_6.rpartition("仅返回JSON：")[0]
        assert "编辑反馈可提醒检查、指导表达修订" in rules
    else:
        assert rules == _AUTHORED_REVIEW_RULES
