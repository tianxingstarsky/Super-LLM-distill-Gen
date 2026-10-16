"""Run the real directed answer parser with offline model responses."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.infrastructure import training_workflow as engine
from lib.infrastructure.dialogue_role_messages import question_role_messages
from lib.prompts import get, render
from lib.prompts.workflow_agent_contracts import (
    SFT_DIRECTED_V1_8, SFT_ANSWER_V1_5, QUESTION_USER_V1_2, QUESTION_FIT_V1_3,
)
from lib.prompts.workflow_utterance import QUESTION_USER_V1_3, QUESTION_FIT_V1_4


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


TEXT = "设备维护前必须断电。"


def question_dispatch(messages):
    """Read the literal saved v1 tool or current v2 system dispatch."""
    for message in messages:
        if message.get("name") == "dialogue_dispatch":
            return json.loads(message["content"])
        if message.get("role") == "system" and message.get("content", "").startswith("<internal_dialogue_dispatch>\n"):
            content = message["content"]
            assert content.endswith("\n</internal_dialogue_dispatch>")
            return json.loads(content.removeprefix("<internal_dialogue_dispatch>\n")
                              .removesuffix("\n</internal_dialogue_dispatch>"))
    raise AssertionError("question dispatch missing")


class Writer:
    model = "offline-contract-writer"

    def __init__(self, responses):
        self.responses, self.calls, self.usage = list(responses), [], {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class Reviewer:
    model = "offline-contract-reviewer"

    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        if "adherence" in messages[0]["content"]:
            return json.dumps({"keep": True, "adherence": 5, "reason": "实际行为符合固定任务与指定风格。"})
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "只按实际问题核对；没有把未定对象当已知故障。"})


def prepare(tmp_path, responses, *, separated, styled=False, policy="answer"):
    output = tmp_path / "out"
    options = {"node_generation": {"sft": {"enabled": True, "style": "custom", "instruction": "解释关键关系。"}}} if styled else {}
    run_id = engine.create_run(output, brief="设计普通求助与资料知识应用。", targets=["sft"],
        qa_director={"enabled": True, "question_separation": separated}, **options)
    writer, reviewer = Writer(responses), Reviewer()
    workflow = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=reviewer)
    workflow.stage = "sft"
    question = "又不行了" if policy == "clarify" else "维护前要做什么？"
    unit = {"id": "sample", "source_id": "source", "source_name": "资料", "kind": "document", "text": TEXT,
            "family_id": "family", "qa_contract": {"qa_type": "closed_book", "question": question,
                "visible_context": "", "answer_policy": policy, "guidance": "按实际输入回应，不猜用户对象。",
                "evidence_quotes": [TEXT]}}
    return workflow, writer, reviewer, unit


@pytest.mark.parametrize("separated", [False, True])
def test_styled_missing_reasoning_is_rejected_then_complete_conditional_schema_is_accepted(tmp_path, separated):
    missing = {"answer": "先断电。", "quotes": [TEXT]}
    if not separated:
        missing["question"] = "维护前要做什么？"
    valid = {**missing, "reasoning": "断电是维护的前置条件，必须在维护之前完成。"}
    workflow, writer, _, unit = prepare(tmp_path, [missing, valid], separated=separated, styled=True)
    row, = workflow.directed_sft(unit)
    assert row["status"] == "eligible" and row["repair_attempts"] == 1
    assert len(writer.calls) == 2
    assert row["messages"][-1]["reasoning_content"] == valid["reasoning"]
    for call in writer.calls:
        assert call[-1] == {"role": "user", "content": unit["qa_contract"]["question"]}
        assert "存在generation_style时，仅返回JSON" in call[0]["content"]
        assert "没有generation_style时，仅返回JSON" in call[0]["content"]
        assert "如有generation_style，必须另加非空reasoning字段" not in call[0]["content"]


@pytest.mark.parametrize("separated", [False, True])
def test_style_off_accepts_answer_quotes_without_second_generation_or_fabricated_reasoning(tmp_path, separated):
    response = {"answer": "先断电。", "quotes": [TEXT]}
    if not separated:
        response["question"] = "维护前要做什么？"
    workflow, writer, _, unit = prepare(tmp_path, [response], separated=separated)
    row, = workflow.directed_sft(unit)
    assert row["status"] == "eligible" and len(writer.calls) == 1
    assert "reasoning_content" not in row["messages"][-1]
    assert row["messages"][-1]["content"] == "先断电。"


@pytest.mark.parametrize("separated", [False, True])
def test_closed_book_answer_keeps_supported_knowledge_and_needed_public_citation_without_private_wrapper(tmp_path, separated):
    source_text = "公开的《设备维护手册》规定：设备维护前必须断电。"
    question = "维护前要做什么？给出必要出处。"
    answer = "维护前先断电。这项前提见《设备维护手册》的维护准备规定。"
    response = {"answer": answer, "quotes": [source_text]}
    if not separated:
        response["question"] = question
    workflow, writer, _, unit = prepare(tmp_path, [response], separated=separated)
    unit.update(text=source_text, source_name="PRIVATE_MATERIAL_WRAPPER")
    unit["qa_contract"].update(question=question, evidence_quotes=[source_text])
    row, = workflow.directed_sft(unit)
    assert row["status"] == "eligible" and len(writer.calls) == 1
    assert row["messages"] == [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
    assert row["quotes"] == [source_text]
    assert "PRIVATE_MATERIAL_WRAPPER" not in json.dumps(row["messages"], ensure_ascii=False)
    assert writer.calls[0][-1] == {"role": "user", "content": question}
    system = writer.calls[0][0]["content"]
    assert "资料在私有记忆中不意味着其必要知识禁止进入答案" in system
    assert "不得假装读者见过隐藏上文" in system


@pytest.mark.parametrize("separated", [False, True])
def test_clarify_preserves_ambiguous_fixed_question_and_has_no_fact_quotes_requirement(tmp_path, separated):
    response = {"answer": "你说的是哪件事？", "quotes": []}
    if not separated:
        response["question"] = "又不行了"
    workflow, writer, _, unit = prepare(tmp_path, [response], separated=separated, policy="clarify")
    row, = workflow.directed_sft(unit)
    assert row["status"] == "eligible" and len(writer.calls) == 1
    assert row["messages"] == [{"role": "user", "content": "又不行了"},
                                {"role": "assistant", "content": "你说的是哪件事？"}]
    assert row["quotes"] == []


@pytest.mark.parametrize("prompt_id,version,body", [
    ("workflow.sft_directed", "1.8.0", SFT_DIRECTED_V1_8),
    ("workflow.sft_answer", "1.5.0", SFT_ANSWER_V1_5),
    ("workflow.question_user", "1.3.0", QUESTION_USER_V1_3),
])
def test_historical_answer_contract_is_complete(prompt_id, version, body):
    assert get(prompt_id, version=version).version == version
    assert render(get(prompt_id, version=version)) == body


@pytest.mark.parametrize("prompt_id,version,previous", [
    ("workflow.sft_directed", "1.10.0", SFT_DIRECTED_V1_8),
    ("workflow.sft_answer", "1.7.0", SFT_ANSWER_V1_5),
])
def test_latest_answer_contract_adds_the_versioned_warmup_and_claim_scope(prompt_id, version, previous):
    from lib.prompts.workflow_reasoning import warmup_generation_body, claim_scope_body
    expected = claim_scope_body(warmup_generation_body(previous), phase="generation")
    assert get(prompt_id).version == version
    assert render(get(prompt_id)) == expected


@pytest.mark.parametrize("prompt_id,version,expected", [
    ("workflow.sft_directed", "1.6.0", "d6d22877dede249c7cf5a2b7334dd02cd6ead0d67b90bef2da31dc9c43a04778"),
    ("workflow.sft_answer", "1.3.0", "a7ec9e4df981a100b1570f05c6965aa33601d50ede60988fdbed60d6d5b8c29c"),
    ("workflow.question_user", "1.1.0", "755015ecc88dc578fbf8b4456d84772cebc83ba3f50a6db1020b0eb68652e3f2"),
    ("workflow.sft_directed", "1.7.0", "99f3e4ec71310ee15a9e69d94b86abd2b9f8fb02829ee98832a019c8fbeddfa4"),
    ("workflow.sft_answer", "1.4.0", "13f95971da7ae4c26a6ae47fa1a57cc463147dfb308212d6819dbc0c9d92f7ac"),
])
def test_previous_complete_template_bytes_are_retained(prompt_id, version, expected):
    assert sha256(get(prompt_id, version=version).template.encode()).hexdigest() == expected


@pytest.mark.parametrize("projection,version,new_policy", [(2, "1.2.0", False), (3, "1.1.0", False), (3, "1.2.0", True)])
def test_blind_question_role_definition_is_opt_in_and_does_not_receive_hidden_policy(tmp_path, projection, version, new_policy):
    workflow, writer, _, _ = prepare(tmp_path, [{"message": "又不行了"}], separated=True)
    workflow.recipe["authored_role_projection_version"] = projection
    workflow.recipe["prompts"]["workflow.question_user"]["version"] = version
    workflow.recipe["node_prompt_templates"]["director"]["workflow.question_user"] = render(get("workflow.question_user", version=version))
    data = {"question_brief": {"topic": "普通求助", "communication_goal": "表达尚未明确对象的困惑"},
            "question_rules": "直接与助手交流。"}
    assert workflow.ask(["question-gate"], "generation", "workflow.question_user", data,
        model_stage="director", prompt_stage="director") == {"message": "又不行了"}
    system = writer.calls[0][0]["content"]
    assert ("首轮不依赖隐藏上文，真实困惑可保留未知对象等待澄清" in system) is new_policy
    assert ("没有历史时生成自包含的首条消息" in system) is not new_policy
    dispatch = question_dispatch(writer.calls[0])
    assert dispatch == {"question_brief": {**data["question_brief"], "behaviors": []}}
    assert not any(message.get("name") == "source_memory" for message in writer.calls[0])
    assert "answer_policy" not in dispatch


def test_question_helper_legacy_default_still_uses_exact_old_surface_instruction():
    data = {"question_brief": {"topic": "普通求助", "communication_goal": "表达困惑"}, "question_rules": ""}
    default = question_role_messages("RULES", data)
    assert default == question_role_messages("RULES", data, first_turn_policy_version=1)
    assert default[0]["content"] == (
        "RULES\n用户配置的提问规则：\n\n你只扮演发言者，不掌握内部资料或参考答案。简报仅用于情境与语言行为指导。"
        "从实际可见对话承接下一行动；没有历史时生成自包含的首条消息。"
        "可以提出请求、表达困惑、协商约束或发问，不假称见过资料、不预写助手答案。")


class SourceAwareFit(Writer):
    """Observe real production envelopes while returning fixed offline verdicts."""
    def __init__(self, *, accepted=True):
        super().__init__([])
        self.accepted, self.fit_requests = accepted, []

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.usage["calls"] += 1
        try:
            data = json.loads(messages[-1]["content"])
        except json.JSONDecodeError:
            assert messages[-1] == {"role": "user", "content": "又不行了"}
            return json.dumps({"answer": "你说的是哪件事？", "quotes": []}, ensure_ascii=False)
        if "question" in data:
            assert QUESTION_FIT_V1_4 in messages[0]["content"]
            self.fit_requests.append(deepcopy(data))
            result = {"keep": self.accepted, "answer_policy": "clarify" if self.accepted else "insufficient",
                      "reason": "目标是了解真实未知所指，不依据资料猜领域。" if self.accepted else
                          "PRIVATE_FIT_FEEDBACK：对象版本或沟通目标不匹配，不能借未选行补证。"}
        elif "question_brief" in data:
            result = {"keep": True, "reason": "简报只有宽泛沟通目标。"}
        else:
            result = {"answer": "你说的是哪件事？", "quotes": []}
        return json.dumps(result, ensure_ascii=False)


def fit_workflow(tmp_path, *, question="又不行了", source=False, accepted=True):
    workflow, _, reviewer, unit = prepare(tmp_path, [], separated=True, policy="clarify")
    aware = SourceAwareFit(accepted=accepted)
    questioner = Writer([{"message": question}, {"message": question}])
    workflow.generator, workflow.questioner = aware, questioner
    brief = {"topic": "普通求助", "communication_goal": "表达未知对象的困惑，等待了解所指"}
    unit["qa_contract"].update(question="内部占位设计", question_brief=brief, evidence_quotes=[])
    if source:
        from lib.domain.source_line_ranges import number_source_material
        from lib.infrastructure.workflow_source_context import _save_pack
        text = "设备v1维护前必须断电。\nUNSELECTED_PRIVATE_V2：设备v2的特定阈值为73。"
        material = number_source_material({"unit_id": "source-material", "source_id": "source",
            "source_name": "版本手册", "text": text, "cleaning_status": "accepted",
            "cleaned_text_sha256": engine.digest(text)})
        directory = workflow.path / "source-context"
        (directory / "packs").mkdir(parents=True, exist_ok=True)
        reference = _save_pack(directory, [material])
        workflow.recipe.update(source_context_pack_version=1, source_line_protocol_version=1)
        unit.update(text=text, source_context_pack=reference)
        unit["qa_contract"]["source_line_ranges"] = [{"material_id": material["material_id"], "start_line": 1, "end_line": 1}]
    else:
        unit.pop("text")
        unit.update(kind="brief", task="表达困惑，逐步了解所指；未指定任何具体故障。")
    return workflow, aware, questioner, reviewer, unit


@pytest.mark.parametrize("source", [False, True])
def test_actual_separated_fit_accepts_purposeful_clarify_without_source_facts_or_expanding_selection(tmp_path, source):
    workflow, aware, questioner, _, unit = fit_workflow(tmp_path, source=source)
    original = deepcopy(unit)
    contract = workflow._closed_book_question(unit, unit["qa_contract"], key=["purposeful-clarify"])
    assert contract["question"] == "又不行了" and contract["answer_policy"] == "clarify"
    assert contract.get("source_line_ranges") == unit["qa_contract"].get("source_line_ranges")
    row, = workflow.directed_sft({**unit, "qa_contract": contract})
    assert row["status"] == "eligible" and row["quotes"] == []
    assert row["messages"] == [{"role": "user", "content": "又不行了"},
                                {"role": "assistant", "content": "你说的是哪件事？"}]
    assert unit == original
    assert len(questioner.calls) == 1 and len(aware.fit_requests) == 1
    blind = json.dumps(questioner.calls, ensure_ascii=False)
    assert "answer_policy" not in blind and "source_line_ranges" not in blind
    assert "source_memory" not in blind and "UNSELECTED_PRIVATE_V2" not in blind
    for call in aware.calls:
        assert "UNSELECTED_PRIVATE_V2" not in json.dumps(call, ensure_ascii=False)
    if source:
        fit = aware.fit_requests[0]
        assert fit["source_line_scope"] == "selected"
        assert fit["source_line_ranges"] == contract["source_line_ranges"]


@pytest.mark.parametrize("question", ["设备v2的阈值是73吗？", "谁赢了昨天的球赛？"])
def test_actual_separated_fit_rejection_cannot_expand_source_or_send_private_feedback_to_questioner(tmp_path, question):
    workflow, aware, questioner, _, unit = fit_workflow(tmp_path, question=question, source=True, accepted=False)
    original = deepcopy(unit)
    with pytest.raises(ValueError, match="question_not_suitable"):
        workflow._closed_book_question(unit, unit["qa_contract"], key=["reject-unmatched-question"])
    assert unit == original
    assert len(questioner.calls) == len(aware.fit_requests) == 2
    for fit in aware.fit_requests:
        assert fit["question"] == question
        assert fit["source_line_scope"] == "selected"
        assert fit["source_line_ranges"] == unit["qa_contract"]["source_line_ranges"]
    blind = json.dumps(questioner.calls, ensure_ascii=False)
    assert "PRIVATE_FIT_FEEDBACK" not in blind and "UNSELECTED_PRIVATE_V2" not in blind
    assert "source_line_ranges" not in blind and "answer_policy" not in blind
    assert question_dispatch(questioner.calls[1])["retry_code"] == "question_not_suitable"


def test_new_fit_full_body_is_registered_without_changing_prior_snapshot():
    assert get("workflow.question_fit").version == "1.4.0"
    assert render(get("workflow.question_fit")) == QUESTION_FIT_V1_4
    assert sha256(get("workflow.question_fit", version="1.2.0").template.encode()).hexdigest() == (
        "7a9c3b8fa0a2743b807fb477010f52a61c8a9f38e2b145ca9cce84440cdda06b")
