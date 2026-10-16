"""Versioned authored curricula review actual text without certifying reasoning."""
from copy import deepcopy

import pytest

from lib.domain.workflow_quality import accepted, canonical
from lib.prompts import get, render
from lib.infrastructure.workflow_review_repair import (
    _authored_warmup_requirements, _authored_reasoning_warmup_rules,
)
from lib.prompts.workflow_reasoning import (
    AUTHORED_REASONING_WARMUP_VERSION, REASONING_WARMUP_RESEARCH_SOURCE,
    warmup_generation_instruction, warmup_review_instruction, warmup_repair_instruction,
    warmup_generation_body, warmup_review_body,
)
from lib.prompts.workflow_agent_contracts import (
    SFT_DIRECTED_V1_8, SFT_DIRECTED_V1_9, SFT_ANSWER_V1_5, SFT_ANSWER_V1_6,
)
from tests.test_authored_reasoning_language_review import LanguageProbe
from tests.test_reasoning_concept_review_v3 import CONCEPT_REASONING
from tests.test_turn_review_and_reasoning_repair import SOURCE, REASON, row, reviewed, updates, grade
from tests.test_review_authored_reasoning import ReviewProbe, authored_row, EVIDENCE


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # These probes never construct a provider client or open a network port.
    yield


STYLE = {"enabled": True, "style": "concise", "reasoning_language": "auto"}


def candidate():
    value = row()
    value.pop("reasoning_summary", None)
    value["reasoning_origin"] = "prompt_styled_generation"
    value["messages"][1]["content"] = "先确认断电，再检查。"
    for message in value["messages"]:
        if message["role"] == "assistant":
            message["reasoning_content"] = REASON
    for turn in value["turn_reviews"]:
        turn["reasoning_origin"] = "prompt_styled_generation"
    return value


class WarmupProbe(LanguageProbe):
    def __init__(self, responses=()):
        super().__init__(responses)
        self.recipe.update(authored_reasoning_warmup_version=1,
                           node_generation={"sft": deepcopy(STYLE), "multiturn": deepcopy(STYLE)})


@pytest.mark.parametrize("helper", [warmup_generation_instruction, warmup_review_instruction,
                                     warmup_repair_instruction])
@pytest.mark.parametrize("version", [None, 0, 2, True, "1", 1.0])
def test_unknown_and_bool_versions_cannot_activate_a_new_contract(helper, version):
    assert helper(version) == ""


def test_warmup_teaches_task_relative_dependence_not_fixed_performance_theater():
    assert AUTHORED_REASONING_WARMUP_VERSION == 1
    generation = warmup_generation_instruction()
    for concept in ("目标", "约束", "信息缺口", "解决路径", "候选结论", "支持结论", "调整", "暂缓"):
        assert concept in generation
    for condition in ("不是每题必须依次", "没有错误尝试就不编造回溯", "统一字数", "最短候选", "真实工具"):
        assert condition in generation
    assert "https://arxiv.org/abs/2501.12599" in REASONING_WARMUP_RESEARCH_SOURCE
    assert "https://arxiv.org/abs/2501.12948" in REASONING_WARMUP_RESEARCH_SOURCE
    assert "提示词等同强化学习" in REASONING_WARMUP_RESEARCH_SOURCE
    review = warmup_review_instruction()
    for boundary in ("分别核对最终结果与实际推理", "随后依据有效核对被否定或修正", "未解除", "循环论证",
                     "reasoning_valid=false", "不是缺陷", "不等同可执行核验"):
        assert boundary in review


def test_editable_new_default_preserves_exclusive_native_contract_and_old_body():
    assert SFT_DIRECTED_V1_9.startswith(SFT_DIRECTED_V1_8 + "\n")
    assert SFT_ANSWER_V1_6.startswith(SFT_ANSWER_V1_5 + "\n")
    assert "长推理示范 v1" not in SFT_DIRECTED_V1_8 + SFT_ANSWER_V1_5
    for body in (SFT_DIRECTED_V1_9, SFT_ANSWER_V1_6):
        assert "只有下列两种互斥输出契约" in body
        assert "没有generation_style时保持上面的普通蒸馏契约" in body
        assert "真实原生思考仍由实际通道保存" in body
    old = '仅返回 {"reasoning":"解释","answer":"回答"}。'
    assert warmup_generation_body(old).startswith(old + "\n")
    for repair in (False, True):
        body = warmup_review_body(old, repair=repair)
        assert body.startswith(old + "\n") and "request.authored_reasoning_warmup.version" in body


@pytest.mark.parametrize("change", ["old", "false", "bool", "future", "unconfigured", "disabled", "native", "human"])
def test_only_the_new_trusted_authored_scope_changes_review_request_bytes(change):
    value = candidate()
    value["generation_style"] = {"instruction": "A row cannot activate a curriculum."}
    worker = WarmupProbe([reviewed({"messages": value["messages"]})])
    if change == "old":
        worker.recipe.pop("authored_reasoning_warmup_version")
    elif change in {"false", "bool", "future"}:
        worker.recipe["authored_reasoning_warmup_version"] = {"false": 0, "bool": True, "future": 2}[change]
    elif change == "unconfigured":
        worker.recipe["node_generation"] = {}
    elif change == "disabled":
        worker.recipe["node_generation"]["multiturn"]["enabled"] = False
    else:
        origin = "provider_reasoning" if change == "native" else "human_revision"
        value["reasoning_origin"] = origin
        for turn in value["turn_reviews"]:
            turn["reasoning_origin"] = origin
    legacy = WarmupProbe([reviewed({"messages": value["messages"]})])
    legacy.recipe = deepcopy(worker.recipe)
    legacy.recipe.pop("authored_reasoning_warmup_version", None)
    legacy_value = deepcopy(value)
    assert _authored_warmup_requirements(worker.recipe, "multiturn", value) is None
    assert accepted(worker._review_score("multiturn", value, worker._review_evidence(value), ["item"], ""))
    assert accepted(legacy._review_score("multiturn", legacy_value, legacy._review_evidence(legacy_value), ["item"], ""))
    assert canonical([worker.calls, worker.call_rules]) == canonical([legacy.calls, legacy.call_rules])


def test_turn_override_and_explicit_disabled_multiturn_prevent_authored_reclassification():
    value = candidate()
    worker = WarmupProbe()
    value["turn_reviews"][1]["reasoning_origin"] = "provider_reasoning"
    assert _authored_warmup_requirements(worker.recipe, "multiturn", value) == {
        "version": 1, "stage": "multiturn", "assistant_indices": [1]}
    assert "assistant[3]" not in _authored_reasoning_warmup_rules(worker.recipe, "multiturn", value, phase="review")
    worker.recipe["node_generation"].pop("multiturn")
    assert _authored_warmup_requirements(worker.recipe, "multiturn", value)["stage"] == "sft"
    worker.recipe["node_generation"]["multiturn"] = {**STYLE, "enabled": False}
    assert _authored_warmup_requirements(worker.recipe, "multiturn", value) is None


def test_all_authored_turns_receive_separate_outcome_process_review_and_pinned_style():
    value = candidate()
    worker = WarmupProbe([reviewed({"messages": value["messages"]})])
    worker.recipe["node_generation_prompts"] = {"multiturn": {"concise": "按用户自定风格充分解释。"}}
    value["generation_style"] = {"instruction": "旧稿缺少当前风格。"}
    frozen = canonical(value)
    assert accepted(worker._review_score("multiturn", value, worker._review_evidence(value), ["item"], ""))
    assert len(worker.calls) == 1
    request = worker.calls[0]
    assert request["authored_reasoning_warmup"] == {"version": 1, "stage": "multiturn", "assistant_indices": [1, 3]}
    assert request["candidate"]["messages"] == value["messages"]
    assert request["generation_style"]["instruction"] == "按用户自定风格充分解释。"
    assert "assistant[1], assistant[3]" in worker.call_rules[0]["instruction"]
    assert "分别核对最终结果与实际推理" not in worker.call_rules[0]["instruction"]
    assert "分别核对最终结果与实际推理" in render(get("workflow.review_score"))
    assert [turn["message_index"] for turn in request["supervised_assistant_turns"]] == [1, 3]
    # Only the ordinary receipt may be added; input text and historical style stay exact.
    value.pop("assistant_turn_review_retry")
    assert canonical(value) == frozen


def test_correct_answer_and_later_good_turn_cannot_wash_out_invalid_earlier_process():
    value = candidate()
    verdict = reviewed({"messages": value["messages"]})
    early = verdict["turn_verdicts"][0]
    early.update(keep=False, reasoning_valid=False, reason="目标虽对，但决定性支持桥梁缺失。")
    early["scores"]["reasoning"] = 2
    worker = WarmupProbe([verdict])
    result = worker._review_score("multiturn", value, worker._review_evidence(value), ["item"], "")
    assert not accepted(result) and result["correctness"] == 5 and not result["reasoning_valid"]
    assert result["scores"]["reasoning"] == 2 and "assistant[1]" in result["reason"]
    assert len(worker.calls) == 1


def test_retracted_exploration_remains_inspectable_and_is_not_a_keyword_failure():
    value = candidate()
    explanation = ("候选可以先检查再断电，但这与检查必须以断电为前提冲突，因此否决这个候选。"
                   "可行顺序是先断电再检查，最后记录；记录的后置条件也得到满足。")
    value["messages"][1]["reasoning_content"] = explanation
    worker = WarmupProbe([reviewed({"messages": value["messages"]})])
    assert accepted(worker._review_score("multiturn", value, worker._review_evidence(value), ["item"], ""))
    assert worker.calls[0]["candidate"]["messages"][1]["reasoning_content"] == explanation
    assert "不因候选曾错误就判整段无效" not in worker.call_rules[0]["instruction"]
    assert "不因候选曾错误就判整段无效" in render(get("workflow.review_score"))
    # A model verdict fixture tests delivery and scope, not mathematical truth.


def test_repair_changes_the_actual_rationale_and_cannot_delete_it_to_bypass_the_curriculum():
    value = candidate()
    delta = updates()
    worker = WarmupProbe([{"record": delta, "quotes": [SOURCE], "uncertain": False}])
    repaired, issue = worker._repair_content("multiturn", value, worker._review_evidence(value), ["fix"], grade(False), "")
    assert issue is None and repaired["messages"][0::2] == value["messages"][0::2]
    assert all(repaired["messages"][index]["reasoning_content"] == REASON for index in (1, 3))
    assert "不删reasoning" not in worker.call_rules[0]["instruction"]
    assert "不删reasoning" in render(get("workflow.review_repair"))
    assert worker.calls[0]["authored_reasoning_warmup"]["assistant_indices"] == [1, 3]
    missing = updates()
    for update in missing["assistant_updates"]:
        update.pop("reasoning_content")
        update.pop("reasoning_trace")
    worker = WarmupProbe([{"record": missing, "quotes": [SOURCE], "uncertain": False}])
    repaired, issue = worker._repair_content("multiturn", value, worker._review_evidence(value), ["fix"], grade(False), "")
    assert issue == "authored_reasoning_invalid_text" and repaired == value


def test_warmup_does_not_override_language_v3_or_knowledge_application():
    value = candidate()
    for message in value["messages"]:
        if message["role"] == "assistant":
            message["reasoning_content"] = CONCEPT_REASONING
    worker = WarmupProbe([reviewed({"messages": value["messages"]})])
    worker.recipe.update(authored_reasoning_language_review_version=3, reasoning_language_contract_version=3,
        knowledge_application_voice_version=3, qa_director={"learning_goal": "knowledge_application"})
    worker.recipe["node_generation"]["multiturn"]["reasoning_language"] = "zh_en"
    assert accepted(worker._review_score("multiturn", value, worker._review_evidence(value), ["item"], ""))
    rules = worker.call_rules[0]["instruction"]
    assert "当前 zh_en v3" in rules and "逐轮知识应用评审" in rules
    assert "显式推理示范作用范围" in rules and "长推理评审 v1" not in rules
    assert len(worker.calls) == 1


@pytest.mark.parametrize("phase", ["review", "repair"])
def test_custom_node_template_is_not_silently_overridden_by_a_hidden_warmup_rubric(phase):
    from lib.infrastructure.training_workflow import Workflow
    value = candidate()
    prompt_id = "workflow.review_score" if phase == "review" else "workflow.review_repair"
    custom = '用户保存的本节点标准：核对指定事实与推理依赖，返回原契约。保留字面JSON {"example":true}。'
    response = (reviewed({"messages": value["messages"]}) if phase == "review" else
                {"record": updates(), "quotes": [SOURCE], "uncertain": False})
    worker = WarmupProbe([response])
    worker.recipe.update(version=9, node_prompt_templates={"review": {prompt_id: custom}})
    assert Workflow.prompt_text(worker, prompt_id, stage="review") == custom
    if phase == "review":
        worker._review_score("multiturn", value, worker._review_evidence(value), ["item"], "")
    else:
        worker._repair_content("multiturn", value, worker._review_evidence(value), ["fix"], grade(False), "")
    hidden = worker.call_rules[0]["instruction"]
    assert "显式推理示范作用范围：assistant[1], assistant[3]" in hidden
    assert "当前request.generation_style来自本次冻结节点配置" in hidden
    assert "原生或人工推理" in hidden
    assert warmup_review_instruction() not in hidden and warmup_repair_instruction() not in hidden
    assert "长推理评审 v1" not in hidden and "长推理返工 v1" not in hidden
    assert "candidate" in worker.calls[0] and len(worker.calls) == 1


def test_cot_and_cot_derived_sft_use_their_own_trusted_authored_node():
    cot = authored_row()
    cot["reasoning_origin"] = "prompt_styled_generation"
    worker = ReviewProbe()
    worker.recipe.update(authored_reasoning_warmup_version=1, node_generation={"cot": deepcopy(STYLE)})
    assert _authored_warmup_requirements(worker.recipe, "cot", cot) == {
        "version": 1, "stage": "cot", "fields": ["candidate.reasoning"]}
    worker._review_score("cot", cot, EVIDENCE, ["item"], "")
    assert "candidate.reasoning" in worker.calls[0]["options"]["instruction"]
    sft = candidate()
    sft.update(reasoning_route="sft_via_cot", reasoning_protocol_version=1)
    assert _authored_warmup_requirements(worker.recipe, "sft", sft)["stage"] == "cot"
    worker.recipe["node_generation"]["cot"]["enabled"] = False
    worker.recipe["node_generation"]["sft"] = deepcopy(STYLE)
    assert _authored_warmup_requirements(worker.recipe, "sft", sft) is None
