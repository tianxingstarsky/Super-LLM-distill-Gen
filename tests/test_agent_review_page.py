"""A selected workflow exposes real, bounded Agent trace review in place."""
from __future__ import annotations

import json

import pytest
from streamlit.testing.v1 import AppTest

from lib.infrastructure.agent_review_driver import FilesystemAgentReviewDriver
from lib.infrastructure.training_workflow import Workflow, create_run


def test_agent_review_english_labels_preserve_source_and_reviewer_text():
    from lib.presentation.streamlit.agent_review_page import _facts, _history, _queue_label

    item = {"ordinal": 7, "review_status": "pending", "independent_replay": True,
            "native": {"messages": [{"role": "user", "content": "原始需求 text"}]},
            "record": {"source_name": "客户文档", "source_location": "第 2 页",
                       "verification": {"method": "recorded", "verified_call_ids": ["one"]}}}
    label = _queue_label(item, "positive", "en")
    facts = _facts(item, "positive", "en")
    history = _history({"decision": "approved", "reviewer": "审阅者", "reason": "需复核中文来源"}, "en")
    assert "Pending review" in label and "原始需求 text" in label
    assert "Example 7" in facts and "Source 客户文档" in facts
    assert "Checked 1 tool calls" in facts
    assert "Approved" in history and "需复核中文来源" in history


def _call(call_id: str, expression: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id,
        "type": "function", "function": {"name": "calculator", "arguments":
                                     json.dumps({"expression": expression})}}]}


def _trace(call_id: str, expression: str, result: str) -> list[dict]:
    return [{"role": "user", "content": expression}, _call(call_id, expression),
            {"role": "tool", "tool_call_id": call_id, "content": result},
            {"role": "assistant", "content": result}]


def _run(tmp_path, *, extra_good=0, long_good=False, extra_trace_cycles=0):
    source = tmp_path / "traces.jsonl"
    good = _trace("good", "2+2", "4")
    if long_good:
        good += _trace("second", "4+4", "8") + _trace("third", "5+5", "10")
    for number in range(6, 6 + extra_trace_cycles):
        good += _trace(f"cycle-{number}", f"{number}+{number}", str(2 * number))
    traces = [good]
    traces.extend(_trace(f"more-{number}", f"{number}+{number}", str(2 * number))
                  for number in range(4, 4 + extra_good))
    traces.append(_trace("bad", "3+3", "7"))
    source.write_text("".join(json.dumps({"messages": row}) + "\n" for row in traces), encoding="utf-8")
    output = tmp_path / "output"
    run_id = create_run(output, sources=[source], targets=["agent"])
    state = Workflow(output, run_id, tmp_path).execute()
    assert state["status"] == "needs_attention"
    return output, run_id


def _ui(output, run_id):
    script = f'''
from pathlib import Path
from lib.bootstrap.workflows import workflow_application
from lib.presentation.streamlit.agent_review_page import render_agent_review
render_agent_review(workflow_application(Path({str(output.parent)!r}), Path({str(output)!r})),
                    {run_id!r}, workspace_id="fixture")
'''
    return AppTest.from_string(script, default_timeout=30).run()


def test_agent_review_stays_with_run_and_saves_versioned_decision(tmp_path, monkeypatch):
    from lib import review_management

    output, run_id = _run(tmp_path)
    monkeypatch.setattr(review_management, "reviewer_identity", lambda: "reviewer")
    ui = _ui(output, run_id)
    assert not ui.exception
    assert ui.radio[0].options and "2\\+2" in ui.radio[0].options[0]
    markup = "".join(item.proto.body for item in ui.get("html"))
    assert "Agent 执行轨迹" in markup and "重放校验" in markup
    assert "来源工件校验" in markup

    candidate_id = FilesystemAgentReviewDriver(output).queue(run_id, limit=1)["items"][0]["candidate_id"]
    ui.button(key=f"agent-review:fixture:{run_id}:approve:{candidate_id}").click().run()
    assert not ui.exception
    driver = FilesystemAgentReviewDriver(output)
    reviewed = driver.queue(run_id, kind="positive", decision="approved")
    assert reviewed["counts"] == {"pending": 0, "approved": 1, "rejected": 0}
    assert reviewed["items"][0]["review"]["reviewer"] == "reviewer"
    assert not ui.radio  # The default pending queue advanced past its only item.


def test_failed_agent_trace_is_read_only_and_retains_failure_evidence(tmp_path):
    output, run_id = _run(tmp_path)
    ui = _ui(output, run_id)
    ui.segmented_control(key=f"agent-review:fixture:{run_id}:kind").set_value("失败轨迹").run()
    assert not ui.exception
    assert ui.radio[0].options and "3\\+3" in ui.radio[0].options[0]
    assert not any(":approve:" in button.key or ":reject:" in button.key for button in ui.button)
    markup = "".join(item.proto.body for item in ui.get("html"))
    assert "Agent 失败轨迹" in markup and "工具结果与本地重放不一致" in markup


def test_agent_review_jumps_to_a_requested_page_without_loading_the_full_queue(tmp_path):
    output, run_id = _run(tmp_path, extra_good=5)
    ui = _ui(output, run_id)
    page_key = f"agent-review:fixture:{run_id}:page:positive:pending:3"
    ui.number_input(key=page_key).set_value(2).run()
    assert not ui.exception
    assert len(ui.radio[0].options) == 3
    assert "6\\+6" in ui.radio[0].options[0]
    assert "2\\+2" not in " ".join(ui.radio[0].options)


def test_unverified_candidate_cannot_be_approved_in_review_ui(tmp_path, monkeypatch):
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver

    output, run_id = _run(tmp_path)
    original = FilesystemWorkflowDriver.agent_review_queue

    def inspect_only(self, *args, **kwargs):
        page = original(self, *args, **kwargs)
        for item in page["items"]:
            if page["kind"] == "positive":
                item["independent_replay"] = False
        return page

    monkeypatch.setattr(FilesystemWorkflowDriver, "agent_review_queue", inspect_only)
    ui = _ui(output, run_id)
    assert not ui.exception
    candidate_id = FilesystemAgentReviewDriver(output).queue(run_id, limit=1)["items"][0]["candidate_id"]
    approve = ui.button(key=f"agent-review:fixture:{run_id}:approve:{candidate_id}")
    reject = ui.button(key=f"agent-review:fixture:{run_id}:reject:{candidate_id}")
    assert approve.disabled and not reject.disabled


def test_agent_review_requires_each_trace_fragment_before_approval(tmp_path, monkeypatch):
    from lib import review_management

    output, run_id = _run(tmp_path, long_good=True)
    monkeypatch.setattr(review_management, "reviewer_identity", lambda: "reviewer")
    candidate_id = FilesystemAgentReviewDriver(output).queue(run_id, limit=1)["items"][0]["candidate_id"]
    prefix = f"agent-review:fixture:{run_id}"
    ui = _ui(output, run_id)
    assert not ui.exception
    assert ui.button(key=f"{prefix}:approve:{candidate_id}").disabled
    assert not ui.button(key=f"{prefix}:reject:{candidate_id}").disabled
    ui.session_state["ui_language"] = "en"
    ui.run()
    assert any("Opened 1 of 2 trace sections" in item.value for item in ui.caption)

    ui.number_input(key=f"{prefix}:preview:{candidate_id}:message-page").set_value(2).run()
    assert not ui.exception
    assert not ui.button(key=f"{prefix}:approve:{candidate_id}").disabled
    ui.button(key=f"{prefix}:approve:{candidate_id}").click().run()
    assert not ui.exception
    assert FilesystemAgentReviewDriver(output).queue(run_id, decision="approved")["matched"] == 1


def test_agent_review_opens_next_unchecked_fragment_after_out_of_order_jump(tmp_path):
    output, run_id = _run(tmp_path, long_good=True, extra_trace_cycles=4)
    candidate_id = FilesystemAgentReviewDriver(output).queue(run_id, limit=1)["items"][0]["candidate_id"]
    prefix = f"agent-review:fixture:{run_id}"
    preview_page_key = f"{prefix}:preview:{candidate_id}:message-page"
    next_key = f"{prefix}:next-unopened:{candidate_id}"
    approve_key = f"{prefix}:approve:{candidate_id}"

    ui = _ui(output, run_id)
    assert not ui.exception
    assert ui.button(key=approve_key).disabled
    ui.number_input(key=preview_page_key).set_value(3).run()
    assert not ui.exception
    assert ui.button(key=approve_key).disabled
    assert any("2 / 3" in item.value for item in ui.caption)

    ui.button(key=next_key).click().run()
    assert not ui.exception
    assert ui.session_state[preview_page_key] == 2
    assert not ui.button(key=approve_key).disabled
    assert not any(button.key == next_key for button in ui.button)


def test_task_detail_opens_agent_review_without_page_navigation(tmp_path):
    output, run_id = _run(tmp_path)
    script = f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.workflows import workflow_application
from lib.presentation.streamlit.task_management_page import render_task_management
st.session_state["ws"] = "fixture"
render_task_management(workflow_application(Path({str(tmp_path)!r}), Path({str(output)!r})),
                       "fixture", lambda args: None, lambda: None)
'''
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    toggle_key = f"task-center-agent-review:fixture:{run_id}"
    assert ui.toggle(key=toggle_key) is not None
    ui.toggle(key=toggle_key).set_value(True).run()
    assert not ui.exception and ui.radio
    assert ui.session_state["task-center-run:fixture"] == run_id


def test_agent_review_data_is_scoped_to_selected_workflow(tmp_path):
    # The review port must not take an arbitrary output path from a UI caller.
    from lib.bootstrap.workflows import workflow_application
    from lib.infrastructure.training_workflow import run_path
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    output_a, run_a = _run(first)
    output_b, run_b = _run(second)
    app_a = workflow_application(tmp_path, output_a)
    assert app_a.agent_review_queue(run_a, limit=1)["run_id"] == run_a
    assert not run_path(output_a, run_b).exists()
    with pytest.raises((OSError, ValueError)):
        app_a.agent_review_queue(run_b, limit=1)
