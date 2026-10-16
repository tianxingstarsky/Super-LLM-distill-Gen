"""Knowledge application explains the problem, not the private evidence pack."""
from copy import deepcopy

import pytest

from lib.domain.knowledge_voice import knowledge_voice_rules
from lib.domain.training_surface import training_surface_issue, training_record_surface_issue
from lib.infrastructure.workflow_node_messages import split_node_rules
from lib.infrastructure.workflow_review_repair import _row_training_surface_issue


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


CONTRACT = {"learning_goal": "knowledge_application", "qa_type": "closed_book", "visible_context": ""}
QUESTION = {"role": "user", "content": "我在搭指令数据流程，怎样安排过滤与回流？"}
VOICE = {"knowledge_application_voice_version": 3, "learning_goal": "knowledge_application"}


@pytest.mark.parametrize("text", [
    "从资料可以直接支持的结构看，应先过滤再回流。",
    "资料明确规定先过滤，再把有效任务回流。",
    "资料未规定过滤阈值，所以不能确定阈值。",
    "材料没有提供具体实现。",
    "The material directly supports this ordering.",
    "The source material does not specify the threshold.",
])
@pytest.mark.parametrize("field", ["reasoning_content", "content"])
def test_actual_source_narration_regressions_fail_in_either_supervised_field(text, field):
    assistant = {"role": "assistant", "content": "先过滤再回流，避免错误被后续轮次复用。",
                 "reasoning_content": "未经检查的候选可能包含错误，先检查再复用。"}
    assistant[field] = "SELF-INSTRUCT is a public method. " + text
    row = {"messages": [QUESTION, assistant], "qa_contract": CONTRACT,
           "reasoning_origin": "provider_reasoning"}
    before = deepcopy(row)
    assert training_record_surface_issue("sft", row, protocol_version=2, **VOICE) == (
        "training_surface_knowledge_source_narration")
    assert row == before  # Native provenance cannot waive an exported defect; no deletion.
    assert training_record_surface_issue("sft", row, protocol_version=2) is None


@pytest.mark.parametrize("reasoning", [
    "过滤必须先于回流，因为未审查的错误样本会在后续采样中被反复使用。",
    "SELF-INSTRUCT 使用约175条手写种子任务；这是该实验的设置，不是所有项目的硬门槛。",
    "用户还没说明任务类型分布，暂时不能给出适合这套流程的固定配比。",
    "如果资料没有提供编码，导入器应要求用户确认，不能静默猜测。",
    "文档解析器负责把PDF转换成可复用文本；提示词控制任务生成的表达。",
    "A data source can be a file or a database. Validate records before reuse.",
])
def test_direct_knowledge_attribution_unknown_user_conditions_and_real_technical_subjects_are_valid(reasoning):
    assert training_surface_issue(reasoning, "先过滤，再回流。", visible_messages=[QUESTION],
                                  contract=CONTRACT, protocol_version=2, **VOICE) is None


def test_real_user_article_can_be_discussed_even_if_the_conversation_started_closed_book():
    article = "材料：候选任务先过滤低质或重复项，剩余有效任务才能进入下一轮采样池。"
    visible = [QUESTION, {"role": "user", "content": "分析这段文章的顺序依据：" + article}]
    assert training_surface_issue("资料明确规定先过滤，随后回流。", "应先过滤。",
        visible_messages=visible, contract=CONTRACT, protocol_version=2, **VOICE) is None


@pytest.mark.parametrize("question", [
    "请分析 SELF-INSTRUCT 论文的主张与局限。",
    "Analyze the SELF-INSTRUCT paper's limitations.",
])
def test_explicit_named_paper_reading_request_can_describe_its_evidence_boundary(question):
    assert training_surface_issue("资料未规定所有领域的最优种子数量。", "该研究设置不构成普遍最优值。",
        visible_messages=[{"role": "user", "content": question}], contract=CONTRACT,
        protocol_version=2, **VOICE) is None


@pytest.mark.parametrize("reasoning", [
    "当资料没有提供编码时，导入器应要求用户确认。",
    "资料没有提供编码时，导入器应要求用户确认。",
    "在文档没有给出币种的情况下，解析器应保留未知状态并要求用户确认。",
])
def test_conditional_rules_about_users_actual_import_data_are_not_source_audits(reasoning):
    visible = [{"role": "user", "content": "设计文档导入器，用户文件没写编码或币种怎么办？"}]
    assert training_surface_issue(reasoning, "标记未知并请求确认。", visible_messages=visible,
                                  contract=CONTRACT, protocol_version=2, **VOICE) is None


def test_current_source_narration_is_not_excused_by_appending_a_conditional_suffix():
    assert training_surface_issue("资料未规定阈值时，我只能检查当前支持范围。", "暂不确定。",
        visible_messages=[QUESTION], contract=CONTRACT, protocol_version=2, **VOICE) == (
            "training_surface_knowledge_source_narration")


@pytest.mark.parametrize("question", ["请分析这张图片中的步骤。", "Explain this diagram."])
def test_explicit_visual_reading_task_keeps_its_source_discussion_exception(question):
    visible = [{"role": "user", "content": [{"type": "text", "text": question},
                                             {"type": "image_url", "image_url": {"url": "image.png"}}]}]
    assert training_surface_issue("资料明确规定先断电再操作。", "先断电。", visible_messages=visible,
        contract=CONTRACT, protocol_version=2, **VOICE) is None


def test_later_article_does_not_retroactively_allow_an_earlier_leak():
    row = {"qa_contract": CONTRACT, "messages": [QUESTION,
        {"role": "assistant", "content": "先过滤。", "reasoning_content": "从资料可以直接支持这个顺序。"},
        {"role": "user", "content": "分析这段文章：候选任务先筛选再进入下一轮采样池。"},
        {"role": "assistant", "content": "文章要求先筛选。", "reasoning_content": "资料明确规定先筛选。"}]}
    assert training_record_surface_issue("multiturn", row, protocol_version=2, **VOICE) == (
        "training_surface_knowledge_source_narration")


def test_user_can_ask_about_the_exact_bad_phrase_as_a_linguistic_object():
    visible = [{"role": "user", "content": "‘从资料可以直接支持’这个措辞有什么问题？"}]
    assert training_surface_issue("‘从资料可以直接支持’的说法没有明确指出具体论证关系。", "应说清依据。",
        visible_messages=visible, contract=CONTRACT, protocol_version=2, **VOICE) is None


def test_trusted_config_activates_voice_without_any_language_style():
    row = {"messages": [QUESTION, {"role": "assistant", "content": "先过滤。",
           "reasoning_content": "资料未规定回流方式。"}], "qa_contract": CONTRACT}
    recipe = {"training_surface_protocol_version": 2, "knowledge_application_voice_version": 3,
              "qa_director": {"learning_goal": "knowledge_application"}}
    assert _row_training_surface_issue("sft", row, recipe) == "training_surface_knowledge_source_narration"
    assert _row_training_surface_issue("sft", row, {"training_surface_protocol_version": 2}) is None
    assert knowledge_voice_rules("knowledge_application", {"learning_goal": "document_reading"})
    assert not knowledge_voice_rules("mixed", {"learning_goal": "document_reading", "qa_type": "grounded"})
    assert knowledge_voice_rules("mixed", CONTRACT)
    assert knowledge_voice_rules("mixed", {"learning_goal": "mixed", "qa_type": "closed_book"})
    assert not knowledge_voice_rules("mixed", {"learning_goal": "mixed", "qa_type": "grounded"})


def test_new_generation_and_review_envelopes_get_the_same_rule_but_legacy_and_cpt_do_not():
    data = {"qa_contract": CONTRACT, "source_context": {"text": "PRIVATE_TEACHER_TEXT"}}
    config = {"learning_goal": "knowledge_application"}
    legacy, original = split_node_rules(data, qa_director=config)
    current, task = split_node_rules(data, qa_director=config, knowledge_application_voice_version=3)
    reviewed, review_task = split_node_rules({"context": data}, qa_director=config,
                                           knowledge_application_voice_version=3)
    assert "知识应用正文规范 v3" in current and reviewed == current
    assert "知识应用正文规范 v3" not in legacy
    assert task == original == data and review_task == {"context": data}
    assert split_node_rules({"text": "清洗文档。"}, qa_director=config,
                            knowledge_application_voice_version=3) == ("", {"text": "清洗文档。"})
