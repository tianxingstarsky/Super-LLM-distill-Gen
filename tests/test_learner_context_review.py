"""Context acceptance never substitutes for or rewrites factual assessment."""
from copy import deepcopy
import json

import pytest

from lib.domain.learner_context_review import learner_context_verdict
from lib.domain.workflow_node_prompts import snapshot_node_prompts, validate_node_prompt_snapshot, active_node_prompt_ids
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.prompts import get, render
from test_review_repair_engine import check
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SOURCE = "The PostgreSQL 18 table XID field is relfrozenxid; the protected XID allocation boundary differs from the warning."


def context(keep=True):
    return {"keep": keep, "context_complete": keep, "leakage_free": keep,
            "reason": "实际对话可以理解，无隐藏前文。" if keep else "回复指向没有出现在实际对话中的hidden_source_987。"}


class Reviewer:
    model = "offline-context-review"
    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}
    def chat(self, wire, **kwargs):
        data = model_request_data(wire)
        self.calls.append((deepcopy(wire), deepcopy(data)))
        self.usage["calls"] += 1
        if data.get("learner_context_only"):
            if "你是实际对话上下文与角色边界审查员" in wire[0]["content"] or "CUSTOM_CONTEXT_REVIEW" in wire[0]["content"]:
                assert "source_evidence" not in data and "candidate" not in data
                return json.dumps(context("hidden_source_987" not in canonical(data["learner_messages"])))
            # Reproduce the old generic scope collision without rewriting it.
            old = check(False)
            old.update(correctness=3, grounded=False, reasoning_valid=True)
            old["scores"] = {name: 3 for name in old["scores"]}
            old["reason"] = "The article did not provide that corrected field."
            return json.dumps(old)
        result = check("FACT_WRONG_SENTINEL" not in canonical(data["candidate"]))
        return json.dumps(result)


def setup(tmp_path, *, legacy=False, custom=None):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["multiturn"],
        review_repair={"mode": "auto", "max_rounds": 0}, package_review={"enabled": True, "node": "jev"},
        **({"node_prompts": {"review": {"workflow.learner_context_check": custom}}} if custom else {}))
    reviewer = Reviewer()
    workflow = engine.Workflow(output, rid, tmp_path, jev=reviewer)
    workflow.stage = "review"
    # This suite isolates the existing context-only judge from its historic
    # aggregate factual score. Prefix/claim scoring has a separate end-to-end
    # suite; retain the exact old role, request and raw-score assertions here.
    workflow.recipe.pop("claim_review_protocol_version")
    workflow.recipe.pop("turn_review_protocol_version")
    workflow.recipe["node_prompt_templates"]["review"]["workflow.review_score"] = render(
        get("workflow.review_score", version="1.11.0"))
    if legacy:
        workflow.recipe.pop("learner_context_review_version")
    question = "资料中写了 relminxid。PostgreSQL 18 该怎么检查冻结边界？"
    row = {"id": "row", "status": "eligible", "source_id": "source", "messages": [
        {"role": "user", "content": question},
        {"role": "assistant", "content": "PostgreSQL 18 的正确字段是 relfrozenxid。警告阈值不等于拒绝分配新XID的保护阈值。"}],
        "qa_contract": {"question": question, "visible_context": "", "qa_type": "closed_book", "answer_policy": "answer"}}
    evidence = {"teacher_evidence": SOURCE, "source_id": "source", "source_kind": "document"}
    return workflow, row, evidence, reviewer


def test_correct_self_contained_document_correction_passes_context_without_article_fact_rubric(tmp_path):
    workflow, row, evidence, reviewer = setup(tmp_path)
    before = deepcopy(row)
    value = workflow._review_score("multiturn", row, evidence, ["row", "grade"], "")
    assert accepted(value) and value == check(True)
    assert row["learner_context_check"] == {**context(), "scope": "actual_training_messages_only", "version": 1}
    assert row["messages"] == before["messages"] and row["qa_contract"] == before["qa_contract"]
    assert reviewer.calls[1][1] == {"learner_messages": before["messages"],
        "qa_contract": {"qa_type": "closed_book", "answer_policy": "answer"}, "learner_context_only": True}
    assert "workflow.learner_context_check" in workflow.recipe["node_prompt_templates"]["review"]
    calls = len(reviewer.calls)
    assert workflow._review_score("multiturn", row, evidence, ["row", "grade"], "") == value
    assert len(reviewer.calls) == calls


def test_true_context_failure_rejects_without_fabricating_a_lower_factual_score(tmp_path):
    workflow, row, evidence, _ = setup(tmp_path)
    row["messages"][-1]["content"] = "按 hidden_source_987 的第六条操作。"
    value = workflow._review_score("multiturn", row, evidence, ["row", "grade"], "")
    assert not accepted(value) and value["keep"] is False
    assert value["correctness"] == 5 and value["scores"] == check(True)["scores"]
    assert value["grounded"] is True and value["reasoning_valid"] is True
    assert row["learner_context_check"]["leakage_free"] is False


def test_bad_current_fact_does_not_get_a_context_pass_or_upgrade(tmp_path):
    workflow, row, evidence, reviewer = setup(tmp_path)
    row["messages"][-1]["content"] = "FACT_WRONG_SENTINEL"
    value = workflow._review_score("multiturn", row, evidence, ["row", "grade"], "")
    assert value == check(False) and len(reviewer.calls) == 1
    assert "learner_context_check" not in row


def test_missing_marker_retains_generic_context_request_and_original_min_score_rule(tmp_path):
    workflow, row, evidence, reviewer = setup(tmp_path, legacy=True)
    value = workflow._review_score("multiturn", row, evidence, ["row", "grade"], "")
    assert not accepted(value) and set(value["scores"].values()) == {3}
    old = reviewer.calls[1][1]
    assert old == {"target": "sft", "candidate": {"messages": row["messages"]}, "learner_messages": row["messages"],
        "source_evidence": None, "qa_contract": {"qa_type": "closed_book", "answer_policy": "answer"},
        "assessment": "learner_context_only", "learner_context_only": True}
    assert "version" not in row["learner_context_check"] and row["learner_context_check"]["grounded"] is False


def test_dedicated_prompt_has_node_override_and_old_snapshot_catalog_stays_literal(tmp_path):
    literal = 'CUSTOM_CONTEXT_REVIEW: return {"keep":true,"context_complete":true,"leakage_free":true,"reason":"ok"}.'
    workflow, row, evidence, reviewer = setup(tmp_path, custom=literal)
    assert accepted(workflow._review_score("multiturn", row, evidence, ["row", "grade"], ""))
    assert literal in reviewer.calls[1][0][0]["content"]
    for version in (19, 20, 21):
        old = snapshot_node_prompts(None, recipe_version=version)
        assert "workflow.learner_context_check" not in old["review"]
        validate_node_prompt_snapshot(old, "SYSTEM", recipe_version=version)
        new = snapshot_node_prompts(None, recipe_version=version, learner_context_review_version=1)
        assert "workflow.learner_context_check" in new["review"]
        validate_node_prompt_snapshot(new, "SYSTEM", recipe_version=version, learner_context_review_version=1)
    assert "workflow.learner_context_check" in active_node_prompt_ids("review", "文档", review_repair={"mode": "auto"})


@pytest.mark.parametrize("field,bad", [("keep", "true"), ("context_complete", 1), ("leakage_free", None),
    ("reason", ""), ("reason", "bad\x00reason"), ("reason", "x" * 4001)])
def test_dedicated_schema_never_coerces_or_invents_context_evidence(field, bad):
    with pytest.raises(ValueError, match="invalid_learner_context_judge_schema"):
        learner_context_verdict({**context(), field: bad})


def test_context_keep_requires_both_checks_and_has_no_fact_score_fields():
    with pytest.raises(ValueError):
        learner_context_verdict({**context(), "context_complete": False})
    with pytest.raises(ValueError):
        learner_context_verdict({**context(), "correctness": 5})
    raw = context(False)
    assert learner_context_verdict(raw) is raw
    body = render(get("workflow.learner_context_check"))
    assert "本阶段不再评correctness、grounded、reasoning_valid" in body
    assert "不要求其每个术语或数字先出现在用户文章中" in body
