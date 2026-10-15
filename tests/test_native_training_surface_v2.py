"""Exported native reasoning has no exception from the learner's text boundary."""
from copy import deepcopy

import pytest

from lib.domain.training_surface import training_record_surface_issue, training_surface_issue


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


INTERNAL = "training_surface_internal_process_reference"
SOURCE = "training_surface_hidden_source_reference"


@pytest.mark.parametrize("reasoning", [
    'We need answer in Chinese, closed_book, no generation_style?',
    'The prompt has answer_policy answer, dialogue_design, expression_design, guidance.',
    'Here there is no generation_style key.',
    '需要按 qa_type distractor 回答；没有 generation_style，返回 JSON。',
    '系统说需要 JSON 格式，quotes 是私有审计元数据。',
    'Quotes are private audit metadata and should not be visible to the learner.',
    'Need output JSON only with answer, quotes.',
    'Need JSON: {answer,quotes}.',
    'We must return JSON {"answer":"...","quotes":[]}.',
    'Need quotes from selected knowledge blocks, not stitched across blocks.',
    'My system prompt says to return a compact response.',
    'The system prompt says to use the hidden rule.',
    '系统提示词中说要补齐当前答案。',
])
def test_actual_failed_native_protocol_features_are_rejected(reasoning):
    assert training_surface_issue(reasoning, "这个流程会生成并筛选指令数据。",
        visible_messages=[{"role": "user", "content": "模型自己生成的数据是怎么做出来的？"}],
        protocol_version=2) == INTERNAL


@pytest.mark.parametrize("question,reasoning,answer", [
    ("JSON API 的 answer 和 quotes 字段如何设计？",
     'We should return JSON with answer and quotes so both fields have a predictable schema.',
     '{"answer":"示例", "quotes":[]}'),
    ("解释 generation_style 和 answer_policy 两个字段的意义。",
     "generation_style 表示生成风格，answer_policy 表示回答策略。", "它们是两个可独立配置的字段。"),
    ("What is a system prompt?", "A system prompt says which role the model should play.",
     "A system prompt can define role and output format."),
    ("请解释论文引用为什么要保留原文与页码。", "引用保留原文可减少转述失真，页码方便读者核对。",
     "可以把参考文献依次编号，正文用编号对应。"),
    ("Self-Instruct 生成指令的流程是什么？", "先有种子任务，再扩写任务，筛选后回池形成迭代。",
     "1. 编写种子任务。\n2. 生成与过滤。\n3. 微调与评测。"),
    ("请用三个步骤解释一元方程的求解。", "第一步移项，第二步合并，第三步除以未知数系数。",
     "1. 移项。\n2. 合并。\n3. 求解。"),
    ("RAG 如何检索和生成？", "RAG 系统根据检索结果组织回答，保留引文用于校验。",
     "检索模块先找相关内容，生成模块再组织回答。"),
    ("quotes 是内部审计字段吗？请解释它的用途。", "quotes 可以作为 private audit metadata 保留。",
     "这个字段的用途取决于具体系统的接口约定。"),
    ("如何设计多轮 API 的工具调用角色？", "工具返回的数据和用户请求应分开，以保留消息来源。",
     "通常用 assistant 发起调用，用 tool 角色返回结果。"),
    ("公司的密码规则如何执行？", "系统规则要求密码满足长度限制，内部规则要求离职时回收账号。",
     "先核查账号权限与密码设置。"),
])
def test_user_visible_technical_topics_and_numbered_answers_are_valid(question, reasoning, answer):
    assert training_surface_issue(reasoning, answer,
        visible_messages=[{"role": "user", "content": question}], protocol_version=2) is None


def row_with_reasoning(body, *, origin="provider_reasoning", version=2):
    return {"training_surface_protocol_version": version, "reasoning_origin": origin,
        "messages": [{"role": "user", "content": "这套方法是如何工作的？"},
                     {"role": "assistant", "content": "先生成指令，再过滤并迭代。", "reasoning_content": body}],
        "provider_reasoning_history": [{"reasoning_content": "I will read source_memory."}],
        "reasoning_summary": "My system prompt says to use hidden evidence."}


@pytest.mark.parametrize("origin", ["provider_reasoning", "synthetic_explanation", "prompt_revision", "none"])
def test_native_and_other_provenance_never_exempt_exported_reasoning(origin):
    row = row_with_reasoning("The prompt has generation_style and answer_policy.", origin=origin)
    before = deepcopy(row)
    assert training_record_surface_issue("sft", row) == INTERNAL
    assert row == before


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "thinking"])
def test_every_exportable_reasoning_alias_is_checked_even_if_another_is_clean(field):
    row = row_with_reasoning("先判断任务目标，再解释关键机制。")
    row["messages"][-1]["reasoning_content"] = "先判断任务目标，再解释关键机制。"
    row["messages"][-1][field] = "We must return JSON with answer and quotes."
    assert training_record_surface_issue("sft", row) == INTERNAL


def test_audit_history_and_summary_do_not_pollute_a_clean_training_surface():
    row = row_with_reasoning("先生成候选指令，再过滤重复项，最后迭代扩展。")
    before = deepcopy(row)
    assert training_record_surface_issue("sft", row) is None
    assert row == before


def test_v1_and_unmarked_rows_keep_their_frozen_behavior():
    row = row_with_reasoning("The prompt has generation_style.", version=1)
    assert training_record_surface_issue("sft", row) is None
    row.pop("training_surface_protocol_version")
    assert training_record_surface_issue("sft", row) is None
    assert training_record_surface_issue("sft", row, protocol_version=2) == INTERNAL


def test_every_multiturn_assistant_is_checked_without_later_messages_granting_visibility():
    row = row_with_reasoning("先解释算法循环与各组件之间的关系。")
    row["messages"].extend([
        {"role": "user", "content": "那它怎么扩展到新指令？"},
        {"role": "assistant", "content": "从已有池中采样。",
         "reasoning_content": "Here there is no generation_style key."},
        {"role": "user", "content": "请解释 generation_style。"},
        {"role": "assistant", "content": "这是一个风格设置字段。", "reasoning_content": "先给出字段含义。"},
    ])
    assert training_record_surface_issue("multiturn", row) == INTERNAL


def test_visible_article_allows_source_reference_but_not_private_generation_schema():
    article = "退货须在签收后30天内申请，商品应保持完好。"
    row = row_with_reasoning("根据所给资料，20天仍在期限内。")
    row["messages"][0]["content"] = article + "\n签收20天能退吗？"
    row["qa_contract"] = {"qa_type": "grounded", "visible_context": article}
    assert training_record_surface_issue("sft", row) is None
    row["messages"][-1]["reasoning_content"] += "The prompt has generation_style."
    assert training_record_surface_issue("sft", row) == INTERNAL


def test_private_memory_is_not_an_article_even_when_native():
    row = row_with_reasoning("根据所给资料，当前规则已经满足。")
    assert training_record_surface_issue("sft", row) == SOURCE


@pytest.mark.parametrize("target", ["dpo", "orpo", "rlaif"])
@pytest.mark.parametrize("branch", ["chosen", "rejected"])
def test_preference_branches_cannot_export_native_protocol_leaks(target, branch):
    row = {"training_surface_protocol_version": 2,
           "prompt": [{"role": "user", "content": "原理是什么？"}],
           "chosen": [{"role": "assistant", "content": "正确解释。", "reasoning_content": "先比较不同条件。"}],
           "rejected": [{"role": "assistant", "content": "错误解释。", "reasoning_content": "忽略了必要条件。"}]}
    row[branch][0]["reasoning_content"] = "Need output JSON only with answer, quotes."
    assert training_record_surface_issue(target, row) == INTERNAL


def test_cot_export_checks_native_text_and_preserves_all_original_characters():
    row = {"question": "它如何生成数据？", "answer": "生成后筛选。",
           "reasoning": ["先判断生成过程。", "Need output JSON only with answer, quotes."],
           "reasoning_origin": "provider_reasoning", "training_surface_protocol_version": 2}
    before = deepcopy(row)
    assert training_record_surface_issue("cot", row) == INTERNAL
    assert row == before


def test_no_content_target_or_malformed_record_is_invented():
    assert training_record_surface_issue("cpt", {"text": "generation_style", "training_surface_protocol_version": 2}) is None
    assert training_record_surface_issue("sft", None, protocol_version=2) is None
