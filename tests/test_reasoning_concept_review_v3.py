"""Versioned concept translation changes requests, never old frozen contracts."""
from copy import deepcopy
import hashlib

import pytest

from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure.workflow_review_repair import (
    _authored_reasoning_language_requirements, _authored_reasoning_language_issue,
    _authored_reasoning_language_review_rules, _authored_reasoning_language_repair_rules,
)
from tests.test_authored_reasoning_language_review import (
    TranslationProbe, STYLE, PAIRED, authored, paired_candidate, ENGLISH,
)
from tests.test_turn_review_and_reasoning_repair import SOURCE, REASON, reviewed, updates


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


CONCEPT_REASONING = (
    "断电是 prerequisite（前置必要条件），在这里表示 only after power is disconnected（仅在电源断开之后），"
    "不是检查完成后可补做的建议。这个条件决定先后依赖，所以要先确认断电再检查。"
    "record after inspection（检查后记录）中的 after 指检查结果已经形成后的时点，不能把记录当作断电前提的替代。"
    "因此安排为先断电、再检查、最后记录；核对这两个时间条件后，顺序与必要条件一致。"
)


class ConceptProbe(TranslationProbe):
    def __init__(self, responses=()):
        super().__init__(responses)
        self.recipe.update(authored_reasoning_language_review_version=3,
                           reasoning_language_contract_version=3)


def concept_candidate():
    value = authored()
    for message in value["messages"]:
        if message["role"] == "assistant":
            message["reasoning_content"] = CONCEPT_REASONING
    return value


def test_v2_entire_review_and_repair_standards_keep_their_original_bytes():
    requirements = _authored_reasoning_language_requirements(TranslationProbe().recipe, "multiturn", paired_candidate())
    assert requirements == [{"message_index": index, "reasoning_language": "zh_en", "protocol_version": 2}
                            for index in (1, 3)]
    assert hashlib.sha256(_authored_reasoning_language_review_rules(requirements).encode()).hexdigest() == (
        "8a6b033bac96a9b0d89d0dc1be376ba351d791e708a2f0464dce1ad2c85a0b61")
    assert hashlib.sha256(_authored_reasoning_language_repair_rules(requirements).encode()).hexdigest() == (
        "4036ecadae6e2ddfe081172cc04c6d6d197d415fcedcefb7bcc4ad5f2359f57c")


def test_v3_requests_concept_translation_that_advances_reasoning_without_requiring_paragraph_duplicates():
    candidate = concept_candidate()
    probe = ConceptProbe([reviewed({"messages": candidate["messages"]})])
    assert accepted(probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], ""))
    assert len(probe.calls) == 1
    assert probe.calls[0]["candidate"]["messages"] == candidate["messages"]
    assert [item["message_index"] for item in probe.calls[0]["supervised_assistant_turns"]] == [1, 3]
    rules = probe.call_rules[0]["instruction"]
    for text in ("关键概念", "忠实", "澄清", "后续论证", "必要中间关系", "数值", "否定", "不确定性", "不再要求每段"):
        assert text in rules
    assert "每个中文分析自然段后紧邻" not in rules
    assert "关键概念" in probe.calls[0]["generation_style"]["instruction"]


@pytest.mark.parametrize("reasoning,defect", [(PAIRED, "whole-paragraph duplicate"),
                                            (REASON + " API LLM JSON.", "decorative terms")])
def test_v3_semantic_judgment_rejects_old_paragraph_duplication_or_term_sprinkling(reasoning, defect):
    # The fake verdict proves delivery and conservative all-turn binding, not
    # that a character detector can establish cross-language understanding.
    candidate = concept_candidate()
    candidate["messages"][1]["reasoning_content"] = reasoning
    response = reviewed({"messages": candidate["messages"]})
    response["turn_verdicts"][0].update(keep=False, reason=defect)
    response["turn_verdicts"][0]["scores"]["instruction"] = 1
    probe = ConceptProbe([response])
    scope = _authored_reasoning_language_requirements(probe.recipe, "multiturn", candidate)
    assert [item["protocol_version"] for item in scope] == [3, 3]
    assert _authored_reasoning_language_issue(candidate, scope) is None
    result = probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], "")
    assert not accepted(result) and "assistant[1]" in result["reason"]
    assert result["scores"]["instruction"] == 1 and result["aggregate_original"]["scores"]["instruction"] == 5
    assert len(probe.calls) == 1


def test_v3_missing_language_feedback_cannot_request_old_whole_paragraph_translation():
    candidate = concept_candidate()
    candidate["messages"][3]["reasoning_content"] = ENGLISH
    probe = ConceptProbe()
    result = probe._review_score("sft", candidate, probe._review_evidence(candidate), ["sample"], "")
    assert result["reason"].startswith("review_repair_reasoning_language_missing_zh:assistant[3]")
    assert "关键概念" in result["reason"] and "跨语言澄清" in result["reason"]
    assert "中文分析段与紧邻" not in result["reason"] and not probe.calls


def test_v3_repair_uses_current_style_and_keeps_v2_history_only_after_acceptance():
    original = authored()
    original["generation_style"] = {"instruction": "每段中文后紧接完整忠实英文译段。", "reasoning_language": "zh_en"}
    frozen = canonical(original)
    delta = updates()
    final = {"messages": deepcopy(original["messages"])}
    for offset, item in enumerate(delta["assistant_updates"]):
        item["reasoning_content"] = CONCEPT_REASONING
        final["messages"][item["message_index"]].update(content=item["content"], reasoning_content=CONCEPT_REASONING)
    probe = ConceptProbe([{"record": delta, "quotes": [SOURCE], "uncertain": False}, reviewed(final)])
    probe.recipe["node_generation_prompts"] = {"multiturn": {"concise": "新的任务论证风格。"}}
    result, = probe._review_repair_item({"target": "multiturn", "record": original})
    assert result["status"] == "eligible" and result["review_repair"]["attempts"] == 1
    assert canonical(original) == frozen and len(probe.calls) == 2
    for request in probe.calls:
        assert "关键概念" in request["generation_style"]["instruction"]
        assert "每段中文后紧接完整忠实英文译段" not in request["generation_style"]["instruction"]
    assert "互译理解返工要求" in probe.call_rules[0]["instruction"]
    assert "不删 reasoning" in probe.call_rules[0]["instruction"]
    assert result["generation_style"] == probe.calls[1]["generation_style"]
    history = result["generation_style_history"][-1]
    assert history["version"] == 3 and history["generation_style"] == original["generation_style"]
    from lib.infrastructure.training_workflow import digest
    assert history["messages_sha256"] == digest(original["messages"])
    assert all(result["messages"][index]["reasoning_content"] == CONCEPT_REASONING for index in (1, 3))


@pytest.mark.parametrize("scope", ["native", "auto", "human"])
def test_v3_language_requirements_do_not_expand_to_native_auto_or_human_explanations(scope):
    candidate = authored()
    probe = ConceptProbe([reviewed({"messages": candidate["messages"]})])
    if scope == "auto":
        probe.recipe["node_generation"]["multiturn"]["reasoning_language"] = "auto"
    else:
        origin = "provider_reasoning" if scope == "native" else "human_revision"
        candidate["reasoning_origin"] = origin
        for turn in candidate["turn_reviews"]:
            turn["reasoning_origin"] = origin
    assert accepted(probe._review_score("multiturn", candidate, probe._review_evidence(candidate), ["sample"], ""))
    assert len(probe.calls) == 1 and "当前 zh_en v3" not in probe.call_rules[0].get("instruction", "")
