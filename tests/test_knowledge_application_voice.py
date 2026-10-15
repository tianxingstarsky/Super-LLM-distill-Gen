"""Closed-book knowledge use is natural without erasing public attribution."""
from copy import deepcopy

import pytest

from lib.domain.workflow_quality import canonical
from lib.infrastructure.workflow_node_messages import split_node_rules


CONFIG = {"learning_goal": "knowledge_application", "user_languages": ["zh", "en"]}
CONTRACT = {"qa_type": "closed_book", "learning_goal": "knowledge_application", "user_language": "en"}


def test_generation_and_review_share_the_same_finite_knowledge_voice_requirement():
    data = {"qa_contract": CONTRACT, "source_context": {"text": "Private teacher evidence."}}
    original = deepcopy(data)
    generated_rules, generated_data = split_node_rules(data, qa_director=CONFIG)
    review = {"context": deepcopy(data), "answer": {"content": "A candidate answer."}}
    reviewed_rules, reviewed_data = split_node_rules(review, qa_director=CONFIG)
    assert reviewed_rules == generated_rules
    assert generated_data == original and reviewed_data == review and data == original
    assert "最终答案与显式推理都直接运用相关知识" in generated_rules
    assert "生成和评审都要检查实际答案及全部显式推理" in generated_rules
    assert "不得把自己查看后台私有资料、接受知识注入或遵循来源包装" in generated_rules
    assert "作为回答的依据或推导步骤" in generated_rules
    assert "本段对话用户主要使用英文" in generated_rules


def test_voice_rule_preserves_named_paper_attribution_and_real_user_documents():
    article = "根据资料、材料指出、原文说是这篇语言学文章讨论的表达。"
    actual = [{"role": "user", "content": "Explain this article and cite its source:\n" + article}]
    data = {"qa_contract": CONTRACT, "learner_messages": actual,
            "generation_style": {"reasoning_language": "zh_en", "instruction": "Use meaningful Chinese and English reasoning."}}
    rules, task = split_node_rules(data, qa_director=CONFIG)
    assert "可以准确说明命名论文的作者提出了什么方法" in rules
    assert "不能把作者的发现、操作和经历说成用户或助手亲自完成" in rules
    assert "若真实用户后来提供文章、询问某篇公开论文或要求出处" in rules
    assert "可以按实际任务引用和解释所需内容" in rules
    assert task["learner_messages"] == actual
    assert canonical(task) == canonical(data)  # No text stripping or keyword substitution.
    assert task["generation_style"]["reasoning_language"] == "zh_en"


@pytest.mark.parametrize("goal", ["document_reading", "mixed"])
def test_other_explicit_learning_goals_do_not_inherit_a_closed_book_voice_ban(goal):
    article = "The source states that the authors evaluated the dataset."
    data = {"qa_contract": {"qa_type": "grounded", "learning_goal": goal,
                            "question": "Summarize the supplied article.", "visible_context": article}}
    rules, task = split_node_rules(data, qa_director={"learning_goal": goal})
    assert "不要写成本次‘根据资料’" not in rules
    assert task["qa_contract"]["visible_context"] == article
    if goal == "document_reading":
        assert "实际训练输入必须包含所需原文" in rules


def test_fixed_human_design_and_unrelated_cpt_calls_keep_their_previous_rules():
    human = {"qa_contract": {**CONTRACT, "human_design": {"seed": {"question": "根据资料解释这条规定。"}}}}
    rules, task = split_node_rules(human, qa_director=CONFIG)
    assert rules == ("\n本次样本用途与语言要求（仅约束候选对话，不约束评审回执语言）：\n"
                     "已有人工问题、资料、角色与语言保持原样；自动用途和语言设置只指导新设计，不翻译或改写固定人工输入。")
    assert task == human
    cpt = {"text": "根据资料清洗语料。", "source": {"text": "The source states something."}}
    assert split_node_rules(cpt, qa_director=CONFIG) == ("", cpt)


def test_legacy_recipe_without_learning_control_keeps_exact_task_and_suffix():
    data = {"qa_contract": {"qa_type": "closed_book"}, "answer": "材料指出一种方法。"}
    assert split_node_rules(data, qa_director={"enabled": True}) == ("", data)
