"""Review-only request projections preserve lineage, current text and resumes."""
from copy import deepcopy
import json

import pytest

from lib.domain.review_context import project_review_request
from lib.domain.workflow_quality import canonical, accepted
from lib.infrastructure import training_workflow as engine
from test_review_repair_engine import check
from tests.test_workflow_production import model_request_data


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SOURCE = "The equipment must be powered off before inspection."
OLD_THOUGHT = "OLD_PROVIDER_THINKING_SENTINEL " + "history " * 3000
OLD_WRONG = "OLD_WRONG_ALL_DATABASES_SENTINEL"


def contract():
    return {"question": "怎样检查？", "visible_context": "", "qa_type": "closed_book", "answer_policy": "answer",
        "revision_context": {"messages": [{"role": "assistant", "content": OLD_WRONG, "reasoning_content": OLD_THOUGHT}]},
        "human_design": {"seed": {"question": "怎样检查？", "answer": "先断电。",
            "revision_context": {"messages": [{"role": "assistant", "content": OLD_WRONG}]},
            "nested": [{"review_repair": {"old": OLD_WRONG}, "answer_requirements": "保持断电前提。"}]}},
        "source_line_ranges": [{"material_id": "real", "start_line": 1, "end_line": 3}],
        "generation_trace": {"old": OLD_THOUGHT}}


def messages():
    return [{"role": "user", "content": "怎样检查？"},
        {"role": "assistant", "content": "带电检查。", "reasoning_content": OLD_THOUGHT},
        {"role": "user", "content": "之后呢？"},
        {"role": "assistant", "content": "继续带电操作。", "reasoning": OLD_THOUGHT, "thinking": OLD_THOUGHT}]


def test_old_request_is_literal_and_new_review_keeps_current_reasoning_and_evidence():
    data = {"target": "multiturn", "candidate": {"messages": messages()}, "qa_contract": contract(),
            "source_evidence": {"teacher_evidence": SOURCE}, "instruction": "修正安全前提。",
            "reasoning_trace": {"current_body_audit": "must remain graded"}}
    raw = deepcopy(data)
    assert project_review_request({}, data, phase="review") is data
    assert project_review_request({}, data, phase="repair") is data
    assert canonical(data) == canonical(raw)
    clean = project_review_request({"review_context_protocol_version": 1}, data, phase="review")
    assert clean["candidate"] is data["candidate"]  # Current fact-grading text is retained.
    assert clean["source_evidence"] is data["source_evidence"]
    assert clean["reasoning_trace"] is data["reasoning_trace"]
    assert clean["candidate"]["messages"][1]["reasoning_content"] == OLD_THOUGHT
    assert "revision_context" not in canonical(clean["qa_contract"])
    assert OLD_WRONG not in canonical(clean["qa_contract"]) and OLD_THOUGHT not in canonical(clean["qa_contract"])
    assert clean["qa_contract"]["human_design"]["seed"]["answer"] == "先断电。"
    assert clean["qa_contract"]["source_line_ranges"] == data["qa_contract"]["source_line_ranges"]
    assert data == raw


def test_multiturn_repair_projection_keeps_role_tool_literals_and_message_positions():
    actual = [{"role": "system", "content": "public\nrule"},
        {"role": "user", "content": [{"type": "text", "text": "literal \\n"}], "reasoning": "recorded user field"},
        {"role": "assistant", "content": "old visible answer", "reasoning_content": OLD_THOUGHT},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call", "function": {"name": "lookup", "arguments": "{}"}}], "thinking": OLD_THOUGHT},
        {"role": "tool", "tool_call_id": "call", "content": {"literal": "observation"}},
        {"role": "user", "content": "then?"}, {"role": "assistant", "content": "old visible final", "reasoning": OLD_THOUGHT}]
    data = {"target": "multiturn", "candidate": {"messages": actual, "tools": [{"type": "function"}]}, "qa_contract": contract()}
    before = deepcopy(data)
    clean = project_review_request({"review_context_protocol_version": 1}, data, phase="repair")
    projected = clean["candidate"]["messages"]
    assert len(projected) == len(actual) and [m["role"] for m in projected] == [m["role"] for m in actual]
    for original, revised in zip(actual, projected):
        expected = {key: value for key, value in original.items()
                    if not (original["role"] == "assistant" and key in {"reasoning_content", "reasoning", "thinking"})}
        assert revised == expected
    assert clean["candidate"]["tools"] == before["candidate"]["tools"] and data == before
    cot = {**data, "target": "cot"}
    assert project_review_request({"review_context_protocol_version": 1}, cot, phase="repair")["candidate"] == cot["candidate"]


class Judge:
    model = "offline-review-context"
    def __init__(self):
        self.calls, self.usage = [], {"calls": 0}
    def chat(self, wire, **kwargs):
        data = model_request_data(wire)
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        if "你是实际对话上下文与角色边界审查员" in wire[0]["content"]:
            return json.dumps({"keep": True, "context_complete": True, "leakage_free": True,
                               "reason": "实际对话完整，不涉及隐藏前文。"})
        visible = data["candidate"].get("messages", [])
        bad_answer = any(message.get("role") == "assistant" and message.get("content") in {
            "带电检查。", "继续带电操作。"} for message in visible)
        return json.dumps(check(OLD_WRONG not in canonical(data) and not bad_answer))


class Repair(Judge):
    def chat(self, wire, **kwargs):
        data = model_request_data(wire)
        self.calls.append(deepcopy(data))
        self.usage["calls"] += 1
        assert OLD_THOUGHT not in canonical(data)
        assert OLD_WRONG not in canonical(data)
        assert data["candidate"]["messages"][1]["content"] == "带电检查。"
        return json.dumps({"record": {"assistant_updates": [
            {"message_index": 1, "content": "先断电，再检查。"},
            {"message_index": 3, "content": "确认安全后记录检查结果。"}]}, "quotes": [SOURCE], "uncertain": False})


def workflow(tmp_path, *, old=False):
    source = tmp_path / "source.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["multiturn"],
        review_repair={"mode": "auto", "max_rounds": 1}, package_review={"enabled": True, "node": "jev"})
    judge, repair = Judge(), Repair()
    probe = engine.Workflow(output, rid, tmp_path, generator=repair, jev=judge)
    probe.stage = "review"
    assert probe.recipe["review_context_protocol_version"] == 1
    if old:
        probe.recipe.pop("review_context_protocol_version")
    row = {"id": "sample", "source_id": "source", "status": "eligible", "messages": messages(),
           "qa_contract": contract(), "reasoning_origin": "provider_reasoning", "reasoning_summary": {"tokens": 100}}
    evidence = {"source_id": "source", "source_kind": "document", "teacher_evidence": SOURCE}
    return probe, row, evidence, judge, repair


@pytest.mark.parametrize("old", [False, True])
def test_real_score_request_separates_historical_answer_and_resume_retains_raw_audit(tmp_path, old):
    probe, row, evidence, judge, _ = workflow(tmp_path, old=old)
    row["messages"][1]["content"] = "先断电，再检查。"
    row["messages"][3]["content"] = "确认安全后记录检查结果。"
    before = deepcopy(row)
    expected_candidate = probe._review_payload("multiturn", row)
    verdict = probe._review_score("multiturn", row, evidence, ["sample", "grade"], "")
    assert accepted(verdict) is (not old)
    raw = judge.calls[0]
    assert raw["candidate"] == expected_candidate  # Existing export alias normalization is unchanged.
    assert raw["candidate"]["messages"][1]["reasoning_content"] == OLD_THOUGHT
    if old:
        assert raw["qa_contract"] == before["qa_contract"]
    else:
        assert OLD_WRONG not in canonical(raw["qa_contract"])
        assert "revision_context" not in canonical(raw["qa_contract"])
    assert row["qa_contract"] == before["qa_contract"] and row["messages"] == before["messages"]
    count = len(judge.calls)
    assert probe._review_score("multiturn", row, evidence, ["sample", "grade"], "") == verdict
    assert len(judge.calls) == count
    checkpoint = engine.read_json(probe.path / "checkpoints/review" / (engine.digest([
        "call", ["sample", "grade", "score"]]) + ".json"))["data"]
    assert checkpoint == verdict  # Normalization never rewrites the original model grade.
    saved = engine.read_json(probe.path / "recipe.json")
    assert saved["review_context_protocol_version"] == 1


def test_new_projection_does_not_upgrade_a_current_wrong_answer(tmp_path):
    probe, row, evidence, judge, _ = workflow(tmp_path)
    checked = probe._review_score("multiturn", row, evidence, ["sample", "wrong"], "")
    assert not accepted(checked) and checked["correctness"] == 1
    assert OLD_WRONG not in canonical(judge.calls[0]["qa_contract"])
    assert judge.calls[0]["candidate"]["messages"][1]["content"] == "带电检查。"


def test_real_multiturn_repair_omits_old_thoughts_but_archives_them_and_keeps_delta_indices(tmp_path):
    probe, row, evidence, _, repair = workflow(tmp_path)
    before = deepcopy(row)
    updated, issue = probe._repair_content("multiturn", row, evidence, ["sample", "fix"], check(False), "修正安全前提。")
    assert issue is None and row == before
    assert [m["role"] for m in updated["messages"]] == [m["role"] for m in before["messages"]]
    assert updated["messages"][0] == before["messages"][0] and updated["messages"][2] == before["messages"][2]
    assert updated["messages"][1] == {"role": "assistant", "content": "先断电，再检查。"}
    assert updated["messages"][3] == {"role": "assistant", "content": "确认安全后记录检查结果。"}
    assert updated["qa_contract"] == before["qa_contract"]
    assert OLD_THOUGHT in canonical(updated["provider_reasoning_history"])
    assert len(repair.calls) == 1
    again, issue = probe._repair_content("multiturn", row, evidence, ["sample", "fix"], check(False), "修正安全前提。")
    assert issue is None and again == updated and len(repair.calls) == 1
