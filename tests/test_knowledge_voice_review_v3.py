"""Knowledge-use grading reads all real outputs without granting citation loopholes."""
from copy import deepcopy

import pytest

from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import workflow_review_repair as review_module
from tests.test_authored_reasoning_language_review import LanguageProbe, authored
from tests.test_reasoning_concept_review_v3 import CONCEPT_REASONING
from tests.test_turn_review_and_reasoning_repair import SOURCE, reviewed, updates


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


class KnowledgeProbe(LanguageProbe):
    def __init__(self, responses=(), *, version=3, goal="knowledge_application"):
        super().__init__(responses)
        self.recipe.pop("authored_reasoning_language_review_version")
        self.recipe["node_generation"] = {}
        self.recipe.update(knowledge_application_voice_version=version,
                           qa_director={"learning_goal": goal})


def knowledge_candidate(*, origin="provider_reasoning"):
    value = authored()
    value["messages"][0]["content"] = "用 SELF-INSTRUCT 的方法帮我设计指令数据生成流程。"
    value["qa_contract"] = {"learning_goal": "knowledge_application", "qa_type": "closed_book"}
    value["reasoning_origin"] = origin
    for turn in value["turn_reviews"]:
        turn["reasoning_origin"] = origin
    return value


@pytest.mark.parametrize("field", ["reasoning_content", "content"])
@pytest.mark.parametrize("target", ["sft", "multiturn", "agent"])
def test_named_public_method_does_not_excuse_source_narration_in_native_reasoning_or_answers(target, field):
    candidate = knowledge_candidate()
    candidate["messages"][1][field] = "SELF-INSTRUCT 是迭代自举方法。从资料可以直接支持先过滤再回流，资料未规定具体阈值。"
    before = deepcopy(candidate)
    probe = KnowledgeProbe()
    result = probe._review_score(target, candidate, probe._review_evidence(candidate), ["sample"], "")
    assert not accepted(result) and not probe.calls
    assert result["reason"].startswith("training_surface_knowledge_source_narration:")
    assert f"candidate.messages[1].{field}" in result["reason"]
    assert candidate == before


def test_semantic_grader_sees_each_full_turn_and_rejects_source_analysis_as_material(monkeypatch):
    candidate = knowledge_candidate()
    candidate["messages"][1]["reasoning_content"] = (
        "先审视这次私有参考证据能够授权哪种措辞，再把这一支持范围当成得出结论的步骤。")
    expected = reviewed({"messages": candidate["messages"]})
    expected["turn_verdicts"][0].update(keep=False, leakage_free=False, critical_error=True,
        reason="candidate.messages[1].reasoning_content: private-evidence review is not knowledge application.")
    expected["turn_verdicts"][0]["scores"]["instruction"] = 1
    # Inspect the semantic request independently of the root-owned narrow
    # pattern guard, which has separate real-record coverage above.
    monkeypatch.setattr(review_module, "_row_training_surface_issue", lambda *_args: None)
    probe = KnowledgeProbe([expected])
    result = probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], "")
    assert len(probe.calls) == 1 and not accepted(result)
    assert "assistant[1]" in result["reason"] and result["aggregate_original"]["scores"]["instruction"] == 5
    assert probe.calls[0]["candidate"]["messages"] == candidate["messages"]
    rules = probe.call_rules[0]["instruction"]
    for text in ("每条实际被监督", "全部显式推理", "包括原生推理", "material", "不能只判 minor",
                 "公开论文", "实际可见", "后来的用户发言", "不按‘资料’字样一律拒绝"):
        assert text in rules


def test_later_visible_article_cannot_retroactively_authorize_first_turn_narration():
    candidate = knowledge_candidate()
    candidate["messages"][1]["reasoning_content"] = "资料未规定具体阈值，因此先核验资料的支持范围。"
    candidate["messages"][2]["content"] = "现在请分析这段资料：维护前必须断电，检查完成之后记录结果。"
    probe = KnowledgeProbe()
    result = probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], "")
    assert "candidate.messages[1].reasoning_content" in result["reason"] and not probe.calls


def test_real_visible_article_and_explicit_reading_request_remain_allowed():
    candidate = knowledge_candidate()
    article = "设备维护前必须断电，断电是检查线路的前置条件，完成维护后记录检查结果。"
    candidate["messages"][0]["content"] = "请分析这段资料的操作顺序：\n" + article
    candidate["messages"][1]["reasoning_content"] = "资料明确规定维护前断电；先断电再检查保留了这一前置条件，检查后记录结果。"
    candidate["qa_contract"].update(qa_type="grounded", visible_context=article)
    probe = KnowledgeProbe([reviewed({"messages": candidate["messages"]})])
    result = probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], "")
    assert accepted(result) and len(probe.calls) == 1
    assert "真实用户明确的阅读/审查请求" in probe.call_rules[0]["instruction"]


@pytest.mark.parametrize("version,goal,contract,active", [
    (None, "knowledge_application", {"learning_goal": "knowledge_application", "qa_type": "closed_book"}, False),
    (2, "knowledge_application", {"learning_goal": "knowledge_application", "qa_type": "closed_book"}, False),
    (3, "document_reading", {"learning_goal": "document_reading", "qa_type": "grounded"}, False),
    (3, "mixed", {"learning_goal": "document_reading", "qa_type": "grounded"}, False),
    (3, "mixed", {"learning_goal": "knowledge_application", "qa_type": "closed_book"}, True),
    (3, "mixed", {"learning_goal": "mixed", "qa_type": "closed_book"}, True),
    (3, "mixed", {"learning_goal": "mixed", "qa_type": "grounded"}, False),
])
def test_only_new_finite_knowledge_use_contract_arms_semantic_voice_standard(version, goal, contract, active):
    candidate = knowledge_candidate()
    candidate["qa_contract"] = contract
    probe = KnowledgeProbe([reviewed({"messages": candidate["messages"]})], version=version, goal=goal)
    assert accepted(probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], ""))
    assert ("逐轮知识应用评审" in probe.call_rules[0].get("instruction", "")) is active


def test_source_narration_native_repair_retains_original_audit_and_full_replacement_reasoning():
    original = knowledge_candidate()
    original["messages"][1]["reasoning_content"] = "从资料可以直接支持先断电，资料未规定具体阈值。"
    frozen = canonical(original)
    delta = updates()
    final = {"messages": deepcopy(original["messages"])}
    for item in delta["assistant_updates"]:
        item["reasoning_content"] = CONCEPT_REASONING
        final["messages"][item["message_index"]].update(content=item["content"], reasoning_content=CONCEPT_REASONING)
    probe = KnowledgeProbe([{"record": delta, "quotes": [SOURCE], "uncertain": False}, reviewed(final)])
    result, = probe._review_repair_item({"target": "multiturn", "record": original})
    assert result["status"] == "eligible" and result["review_repair"]["attempts"] == 1
    assert len(probe.calls) == 2 and canonical(original) == frozen
    assert "知识应用返工" in probe.call_rules[0]["instruction"]
    assert "不能因前文出现论文名" in probe.call_rules[0]["instruction"]
    assert all(result["messages"][index]["reasoning_content"] == CONCEPT_REASONING for index in (1, 3))
    assert result["reasoning_origin"] == "prompt_revision"
    assert original["messages"][1]["reasoning_content"] in canonical(result["provider_reasoning_history"])


def test_new_narration_issue_does_not_strand_initial_repairable_surface_failure(monkeypatch):
    original = knowledge_candidate(origin="prompt_styled_generation")
    original["messages"][1]["reasoning_content"] = "资料未规定具体阈值，先核验资料。"
    actual = review_module._payload_issue
    monkeypatch.setattr(review_module, "_payload_issue", lambda target, record:
        "training_surface_knowledge_source_narration" if record["messages"][1]["reasoning_content"] == original["messages"][1]["reasoning_content"]
        else actual(target, record))
    delta = updates()
    final = {"messages": deepcopy(original["messages"])}
    for item in delta["assistant_updates"]:
        item["reasoning_content"] = CONCEPT_REASONING
        final["messages"][item["message_index"]].update(content=item["content"], reasoning_content=CONCEPT_REASONING)
    probe = KnowledgeProbe([{"record": delta, "quotes": [SOURCE], "uncertain": False}, reviewed(final)])
    result, = probe._review_repair_item({"target": "multiturn", "record": original})
    assert result["status"] == "eligible"
    assert result["review_repair"]["initial_surface_issue"] == "training_surface_knowledge_source_narration"
    assert len(probe.calls) == 2 and all(result["messages"][index].get("reasoning_content") for index in (1, 3))
