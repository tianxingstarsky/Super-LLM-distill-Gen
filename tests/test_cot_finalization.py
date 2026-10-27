"""Coherent final delivery and preserved drafts, without paid model requests."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.contracts import training_record
from lib.domain.workflow_node_prompts import active_node_prompt_ids
from test_integrated_reflection import NoReviewPort, body, recipe
from test_generation_node_use_cases import conversation, REASONING, ANSWER


def draft():
    value = body()
    value.pop("answer")
    value["reasoning"] = "我暂时把检查线路排在断电之前，但这个顺序仍需与操作前提核对。"
    return value


def test_finalizer_replaces_reasoning_and_answer_together_without_certifying_quality():
    first, final = draft(), body()
    before = deepcopy(first)
    port = NoReviewPort([first, final])
    row, = GenerateCot(port, recipe("draft_then_finalize")).generate_styled(
        conversation(), {"instruction": "自然推敲，保留条件。"})
    assert row["status"] == "eligible" and row["repair_attempts"] == 0
    assert [call[2] for call in port.calls] == ["workflow.cot_rationale", "workflow.cot_generate"]
    assert all(call[4]["structured_response_attempts"] == 1 for call in port.calls)
    assert "reference_answer" not in port.calls[0][3] and "reference_answer" not in port.calls[1][3]
    assert port.calls[1][3]["feedback"]["draft_to_finalize"] == before
    assert port.calls[1][3]["prompt"] == conversation()["messages"][:-1]
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    audit = row["cot_finalization"]
    assert audit["draft"] == first == before and audit["reasoning_changed"] is True
    assert audit["independent_review"] is False and "passed" not in audit
    exported = training_record("cot", row)
    assert exported == {"question": conversation()["messages"][:-1], "reasoning": [REASONING], "answer": ANSWER}


@pytest.mark.parametrize("bad_stage", ["draft", "final"])
def test_malformed_stage_retains_both_bodies_without_rebuying_generation(bad_stage):
    first, final = draft(), body()
    (first if bad_stage == "draft" else final)["reasoning_trace"] = {}
    port = NoReviewPort([first, final])
    row, = GenerateCot(port, recipe("draft_then_finalize")).generate_styled(
        conversation(), {"instruction": "保留完整论证。"})
    assert row["status"] == "quarantined"
    assert len(port.calls) == (1 if bad_stage == "draft" else 2)
    assert row["feedback"]["cot_finalization"]["draft"] == first
    assert row["feedback"]["raw_response"] == (first if bad_stage == "draft" else final)


def test_finalization_lists_both_editable_authoring_prompts_and_preserves_optional_review():
    config = recipe("draft_then_finalize")["node_generation"]
    assert active_node_prompt_ids("cot", "文档", node_generation=config) == (
        "workflow.cot_rationale", "workflow.cot_generate")
    del config["cot"]["self_check"]
    assert active_node_prompt_ids("cot", "文档", node_generation=config) == (
        "workflow.cot_rationale", "workflow.cot_generate", "workflow.rationale_check", "workflow.style_check")


def test_disk_workflow_resumes_after_finalizer_interrupt_without_rebuying_draft(tmp_path):
    from lib.infrastructure import training_workflow as engine
    from test_workflow_styled_generation import run, model_task_data
    output, run_id, path = run(tmp_path, targets=["cot"], conversation=True,
        node_generation=recipe("draft_then_finalize")["node_generation"])

    class Author:
        def __init__(self):
            self.calls = []
            self.interrupt = True

        def chat(self, messages, **kwargs):
            self.calls.append(deepcopy(messages))
            dispatch = model_task_data(messages)
            candidate = dispatch.get("feedback", {}).get("draft_to_finalize")
            if candidate is None:
                return json.dumps(draft(), ensure_ascii=False)
            assert candidate == draft()
            # The draft cannot replace the real user turn or masquerade as
            # the assistant's public history.
            assert [m["content"] for m in messages if m["role"] == "user"] == ["设备应如何维护？"]
            if self.interrupt:
                self.interrupt = False
                raise RuntimeError("test_finalizer_interrupted")
            return json.dumps(body(), ensure_ascii=False)

    author = Author()
    state = engine.Workflow(output, run_id, tmp_path, generator=author).execute()
    assert state["status"] == "failed" and len(author.calls) == 2
    state = engine.Workflow(output, run_id, tmp_path, generator=author).execute()
    assert state["status"] == "completed" and len(author.calls) == 3
    record, = engine.read_json(path / "artifacts/cot.records.json")
    assert record["cot_finalization"]["draft"] == draft()
    exported = (path / "artifacts/cot.jsonl").read_bytes()
    assert json.loads(exported)["reasoning"] == [REASONING]
    engine.Workflow(output, run_id, tmp_path, generator=author).execute()
    assert len(author.calls) == 3 and (path / "artifacts/cot.jsonl").read_bytes() == exported
