"""Explicit leakage checks operate on the full authored training surface only."""
from copy import deepcopy

import pytest

from lib.domain.training_surface import training_surface_issue


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("body", [
    "根据所给资料，条件成立。", "依据提供的文档，期限是30天。",
    "所提供资料中指出温度应保持稳定。", "结合给定材料，可以得到结果。",
    "根据检索结果，故障源于阀门。", "内部参考记忆说明申请必须提前提交。",
    "教师模型提供的证据表明该结果正确。",
    "According to the provided materials, the deadline is thirty days.",
    "Based on retrieved results, close the valve.",
])
def test_hidden_source_reference_is_checked_in_both_reasoning_and_answer(body):
    issue = "training_surface_hidden_source_reference"
    assert training_surface_issue(["前面的内容正常。", body], "结论正常。") == issue
    assert training_surface_issue("解释正常。", "结论正常。\n" + body) == issue


@pytest.mark.parametrize("body", [
    "我先读取 source_memory 再根据其中的规则回答。", "依据 qa_contract 生成本轮答案。",
    "首先检查 dialogue_dispatch，之后输出结论。", "I will read teacher_evidence before generating this answer.",
    "系统提示词要求我输出足够长的推理。", "开发者提示规定要写核对步骤。",
    "指导员分配了这个问题。", "为了满足生成要求，我补充推理。",
    "为了满足评分要求，答案写成如下形式。", "为了满足风格要求，采用这个说法。",
])
def test_explicit_generation_process_reference_is_rejected(body):
    assert training_surface_issue(body, "业务结论。") == "training_surface_internal_process_reference"


def test_article_reference_requires_the_exact_article_in_actual_messages():
    article = "退货须在签收后30天内申请，商品应保持完好。"
    contract = {"qa_type": "grounded", "visible_context": article}
    body = "根据提供的资料，签收后20天且商品完好，仍在退货期限内。"
    visible = [{"role": "user", "content": article + "\n签收后20天能退吗？"}]
    assert training_surface_issue(body, "可以申请退货。", visible_messages=visible, contract=contract) is None
    assert training_surface_issue(body, "可以申请退货。", contract=contract) == "training_surface_hidden_source_reference"
    assert training_surface_issue(body, "可以申请退货。", visible_messages=visible,
                                  contract={**contract, "qa_type": "closed_book"}) == "training_surface_hidden_source_reference"


def test_legacy_document_question_without_contract_allows_actual_visible_article():
    article = "资料：\n退货须在签收后30天内申请，商品应保持完好。\n\n用户消息：\n签收20天能退吗？"
    visible = [{"role": "user", "content": article}]
    body = "根据给定资料，20天未超过30天。"
    assert training_surface_issue(body, "商品完好时可以申请。", visible_messages=visible) is None
    assert training_surface_issue(body, "商品完好时可以申请。", visible_messages=visible,
                                  contract={"qa_type": "closed_book"}) == "training_surface_hidden_source_reference"


@pytest.mark.parametrize("body", [
    "我有一篇文章，请按资料回答。", "资料：\n\n用户消息：\n期限是多久？",
    "资料：\n没有\n\n用户消息：\n期限是多久？",
])
def test_a_claim_or_empty_article_wrapper_does_not_authorize_source_references(body):
    assert training_surface_issue("根据给定资料，期限30天。", "30天。",
                                  visible_messages=[{"role": "user", "content": body}]) == "training_surface_hidden_source_reference"


@pytest.mark.parametrize("qa_type", ["grounded", "distractor", "multi_source", "partial"])
def test_visible_article_allows_source_reference_but_never_private_teacher_reference(qa_type):
    article = "实际文章：室内湿度应保持在40%至60%。"
    contract = {"qa_type": qa_type, "visible_context": article}
    visible = [{"role": "user", "content": [{"type": "text", "text": article}]}]
    assert training_surface_issue("根据所给资料，湿度50%合适。", "50%。",
                                  visible_messages=visible, contract=contract) is None
    assert training_surface_issue("教师证据指出湿度50%合适。", "50%。",
                                  visible_messages=visible, contract=contract) == "training_surface_hidden_source_reference"


def test_contract_metadata_and_generated_assistant_content_cannot_grant_article_visibility():
    article = "资料包含一个秘密教师条件。"
    contract = {"qa_type": "grounded", "visible_context": article}
    messages = [{"role": "user", "content": "回答问题", "source_memory": article},
                {"role": "assistant", "content": article}]
    assert training_surface_issue("根据给定资料得知条件。", "结果。", visible_messages=messages,
                                  contract=contract) == "training_surface_hidden_source_reference"


@pytest.mark.parametrize("question,answer", [
    ("解释 source_memory 字段的作用。", "source_memory 字段可以保存资料正文，不应当覆盖系统规则。"),
    ("RAG 的检索结果为什么要保留来源？", "检索结果可以附带来源，便于核对事实。"),
    ("系统提示词是什么？", "系统提示词要求模型遵守角色和输出规则。"),
    ("介绍开发者提示的作用。", "开发者提示规定应用行为，可以设置输出格式。"),
    ("‘根据所给资料’这种表达有什么问题？", "‘根据所给资料’这种措辞依赖没有展示的来源，应避免。"),
    ("What is a system prompt?", "A system prompt requires consistent behavior across turns."),
])
def test_technical_domain_discussion_and_quoted_wording_remain_valid(question, answer):
    assert training_surface_issue("讨论这个领域术语。", answer,
                                  visible_messages=[{"role": "user", "content": question}]) is None


def test_technical_question_does_not_allow_a_description_of_own_hidden_process():
    messages = [{"role": "user", "content": "解释系统提示词和 source_memory。"}]
    assert training_surface_issue("系统提示词要求我输出两段。", "结果。", visible_messages=messages)
    assert training_surface_issue("我先读取 source_memory 再回答。", "结果。", visible_messages=messages)


def test_domain_facts_and_user_visible_role_constraints_are_not_broad_keyword_matches():
    assert training_surface_issue("根据压力变化判断，阀门可能关闭。资料整理须先核对日期。",
                                  "按你的要求，用温和的客服语气说明处理方式。") is None


@pytest.mark.parametrize("question,answer", [
    ("请解释 RAG 的检索与生成如何配合。", "RAG 系统根据检索结果生成回答。"),
    ("检索增强生成模型如何工作？", "检索增强生成模型依据检索结果组织回答。"),
    ("How does RAG combine retrieval and generation?", "A RAG system generates an answer based on retrieved results."),
    ("Explain retrieval-augmented generation.", "Retrieval-augmented generation models produce answers based on search results."),
])
def test_explicit_rag_topic_allows_general_third_party_system_behavior(question, answer):
    assert training_surface_issue("先说明该系统的检索阶段与生成阶段。", answer,
        visible_messages=[{"role": "user", "content": question}], contract={"qa_type": "closed_book"}) is None


@pytest.mark.parametrize("answer", [
    "我根据检索结果给你回答。", "本次我们依据隐藏知识库回答。",
    "RAG 系统根据检索结果生成回答，我也根据检索结果回答你。",
    "RAG 系统根据内部参考记忆生成答案。",
    "I answer based on retrieved results.", "A RAG system generates my answer based on retrieved results.",
    "RAG models use private evidence to produce an answer.",
])
def test_rag_topic_never_permits_current_response_or_private_evidence_references(answer):
    assert training_surface_issue("解释。", answer, visible_messages=[{
        "role": "user", "content": "请解释 RAG。 Explain RAG."}], contract={"qa_type": "closed_book"}) == "training_surface_hidden_source_reference"


def test_general_system_behavior_exception_requires_an_explicit_visible_rag_topic():
    assert training_surface_issue("解释。", "RAG 系统根据检索结果生成回答。",
        visible_messages=[{"role": "user", "content": "退货期限多久？"}],
        contract={"qa_type": "closed_book"}) == "training_surface_hidden_source_reference"


def test_authored_check_cannot_claim_alignment_with_an_invisible_source():
    body = "核对：关键前提和结论按资料逐项对齐。"
    assert training_surface_issue(body, "结论。", visible_messages=[{"role": "user", "content": "如何处理回卷风险？"}],
        contract={"qa_type": "closed_book"}) == "training_surface_hidden_source_reference"
    article = "本次用户实际可见的文章正文，明确介绍数据库维护条件。"
    assert training_surface_issue(body, "结论。", visible_messages=[{"role": "user", "content": article}],
        contract={"qa_type": "grounded", "visible_context": article}) is None


def test_general_document_check_instructions_are_not_hidden_source_claims():
    assert training_surface_issue("先检查文件是否完整。", "你可以按资料逐项核对所需字段。",
        visible_messages=[{"role": "user", "content": "怎样核查资料字段是否完整？"}],
        contract={"qa_type": "closed_book"}) is None


def test_private_audit_dictionary_is_not_scanned_and_inputs_are_not_mutated():
    trace = {"source_memory": "根据所给资料。", "reasoning_trace": {"source": "内部参考记忆"}}
    original = deepcopy(trace)
    assert training_surface_issue(trace, "纯业务答案。") is None
    assert trace == original


def _cot_row(body, *, origin="prompt_styled_generation", marker=True):
    return {"question": "签收20天能退货吗？", "answer": "可以。", "reasoning": [body],
            "reasoning_origin": origin, **({"training_surface_protocol_version": 1} if marker else {}),
            "reasoning_trace": {"source_memory": "根据所给资料。"}}


def test_review_payload_check_is_opted_in_and_does_not_touch_provider_reasoning():
    from lib.infrastructure.workflow_review_repair import _payload_issue
    body = "根据所给资料，签收20天可以申请。"
    assert _payload_issue("cot", _cot_row(body)) == "training_surface_hidden_source_reference"
    assert _payload_issue("cot", _cot_row(body, marker=False)) is None
    provider = _cot_row(body, origin="provider_reasoning")
    original = deepcopy(provider)
    assert _payload_issue("cot", provider) is None
    assert provider == original


def test_review_recipe_opt_in_only_applies_to_authored_origins():
    from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
    recipe = {"training_surface_protocol_version": 1}
    body = "系统提示词要求我输出解释。"
    assert _row_training_surface_issue("cot", _cot_row(body, marker=False), recipe)
    assert _row_training_surface_issue("cot", _cot_row(body, marker=False), {}) is None
    assert _row_training_surface_issue("cot", _cot_row(body, origin="synthetic_explanation", marker=False), recipe) is None
    assert _row_training_surface_issue("cot", _cot_row(body, origin="provider_reasoning", marker=False), recipe) is None


@pytest.mark.parametrize("source_phrase", [
    "这块资料里没有可套用的通用结论。", "这份资料中未提供起算方式。",
    "这些材料指出需要先停机。", "这段上下文并没有额外规则。",
])
def test_current_hidden_source_self_reference_is_rejected(source_phrase):
    assert training_surface_issue("解释正常。", source_phrase,
        visible_messages=[{"role": "user", "content": "退款如何处理？"}],
        contract={"qa_type": "closed_book"}) == "training_surface_hidden_source_reference"


@pytest.mark.parametrize("origin", ["provider_reasoning", "prompt_styled_generation", "synthetic_explanation"])
def test_new_recipe_scans_all_visible_answers_without_scanning_native_reasoning(origin):
    from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
    row = _cot_row("PRIVATE_NATIVE: 我读取 source_memory 并遵循 qa_contract。", origin=origin, marker=False)
    row["answer"] = "这块资料里没有可套用的通用结论。"
    original = deepcopy(row)
    assert _row_training_surface_issue("cot", row, {"training_surface_protocol_version": 1}) in {
        "training_surface_hidden_source_reference", "training_surface_internal_process_reference"}
    assert _row_training_surface_issue("cot", row, {}) is None
    assert row == original
    row["answer"] = "需要明确订单类型和收货时间，再判断适用规则。"
    if origin != "prompt_styled_generation":
        assert _row_training_surface_issue("cot", row, {"training_surface_protocol_version": 1}) is None
    else:
        assert _row_training_surface_issue("cot", row, {"training_surface_protocol_version": 1}) == "training_surface_internal_process_reference"


@pytest.mark.parametrize("row_marker", [False, True])
def test_native_sft_leaked_answer_is_checked_but_original_chain_and_audits_are_untouched(row_marker):
    from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
    row = {"reasoning_origin": "provider_reasoning", "messages": [
        {"role": "user", "content": "设备检查如何准备？"},
        {"role": "assistant", "content": "这些资料指出需要先断电。",
         "reasoning_content": "PRIVATE_NATIVE: 根据所给资料。我读取 source_memory 并遵循 qa_contract。"}],
        "reasoning_trace": {"source_memory": "private audit"},
        **({"training_surface_protocol_version": 1} if row_marker else {})}
    original = deepcopy(row)
    recipe = {} if row_marker else {"training_surface_protocol_version": 1}
    assert _row_training_surface_issue("sft", row, recipe) == "training_surface_hidden_source_reference"
    assert row == original
    row["messages"][-1]["content"] = "先确认允许停机，再断电检查。"
    assert _row_training_surface_issue("sft", row, recipe) is None
    assert row["messages"][-1]["reasoning_content"] == original["messages"][-1]["reasoning_content"]


def test_native_visible_article_answer_can_refer_to_the_article():
    from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
    article = "退货须在签收后30天内申请，商品应保持完好。"
    row = {"reasoning_origin": "provider_reasoning", "qa_contract": {"qa_type": "grounded", "visible_context": article},
           "question": [{"role": "user", "content": article + "\n这份资料给出的期限多久？"}],
           "reasoning": ["PRIVATE_NATIVE: 我读取 source_memory。"], "answer": "这份资料指出期限为30天。"}
    assert _row_training_surface_issue("cot", row, {"training_surface_protocol_version": 1}) is None


@pytest.mark.parametrize("question,answer", [
    ("如何将这些资料转换为JSON？", "这些资料可以逐行读取，并将字段映射为JSON键。"),
    ("RAG 系统如何处理检索文本？", "RAG 系统根据检索结果生成回答。"),
])
def test_normal_file_processing_and_rag_domain_behavior_are_not_banned(question, answer):
    assert training_surface_issue("先说明处理过程。", answer,
        visible_messages=[{"role": "user", "content": question}], contract={"qa_type": "closed_book"}) is None


def test_review_styled_final_turn_does_not_scan_earlier_native_or_obsolete_audit():
    from lib.infrastructure.workflow_review_repair import _row_training_surface_issue
    row = {"training_surface_protocol_version": 1, "reasoning_origin": "prompt_styled_generation",
           "messages": [{"role": "user", "content": "先介绍规则。"},
                        {"role": "assistant", "content": "30天内申请。", "reasoning_content": "根据所给资料。"},
                        {"role": "user", "content": "签收20天能退吗？"},
                        {"role": "assistant", "content": "可以。", "reasoning_content": "20小于30，满足期限。"}],
           "turn_reviews": [{"end_message_index": 1, "reasoning_origin": "provider_reasoning"},
                            {"end_message_index": 3, "reasoning_origin": "provider_reasoning"}],
           "reasoning_trace": {"source_memory": "根据所给资料。"}}
    assert _row_training_surface_issue("sft", row) is None
    row["messages"][-1]["reasoning_content"] = "系统提示词要求我写核对。"
    assert _row_training_surface_issue("sft", row) == "training_surface_internal_process_reference"


def test_review_score_rejects_explicit_surface_leak_without_a_model_call():
    from lib.infrastructure.workflow_review_repair import WorkflowReviewRepair
    from lib.domain.workflow_quality import accepted, verdict

    class Probe(WorkflowReviewRepair):
        recipe = {"training_surface_protocol_version": 1}
        state = {"stages": {"review": {}}}

        def save(self, **kwargs):
            pass

        def ask(self, *args, **kwargs):
            pytest.fail("Obvious leaked production instructions must be rejected before a paid judge call")

    check = Probe()._review_score("cot", _cot_row("系统提示词要求我写长解释。"), {}, [], "")
    assert verdict(check) == check and not accepted(check)
    assert check["reason"] == "training_surface_internal_process_reference"
