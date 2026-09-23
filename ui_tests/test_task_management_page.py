"""Exercise the standalone task-management view with persisted workflow runs."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from lib.infrastructure.training_workflow import Workflow, create_run


ROOT = Path(__file__).resolve().parent.parent


def task_screen(output: str, workspace_id: str) -> None:
    from pathlib import Path
    import streamlit as st
    from lib.application.workflow_service import WorkflowApplication
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
    from lib.presentation.streamlit.task_management_page import render_task_management

    application = WorkflowApplication(FilesystemWorkflowDriver(Path(output).parent, Path(output)))

    def begin(command):
        st.session_state["task_commands"] = [*st.session_state.get("task_commands", []), command]

    def new_workflow():
        st.session_state["new_workflow_requested"] = True

    render_task_management(application, workspace_id, begin, new_workflow)


def test_task_cards_select_real_run_and_show_its_nodes_and_events(tmp_path):
    source = tmp_path / "guide.txt"
    source.write_text("维护前先断电，检查线路并记录结果。", encoding="utf-8")
    output = tmp_path / "out"
    complete_id = create_run(output, sources=[source], targets=["cpt"], name="已完成的文档任务")
    assert Workflow(output, complete_id, ROOT).execute()["status"] == "completed"
    queued_id = create_run(output, sources=[source], targets=["cpt"], name="待处理任务")

    app = AppTest.from_function(task_screen, args=(str(output), "test-space"), default_timeout=15)
    app.run()
    assert not app.exception
    cards = [item for item in app.button if item.key and item.key.startswith("task-card:test-space:")]
    assert len(cards) == 2
    card_markup = "".join(item.value for item in app.get("html") if isinstance(item.value, str))
    assert 'aria-valuemax="3" aria-valuenow="0"' in card_markup
    assert 'aria-valuemax="3" aria-valuenow="3"' in card_markup
    assert "CPT 预训练语料" in card_markup
    assert app.session_state["task-center-run:test-space"] == queued_id
    assert any(item.key == f"flow-node:{queued_id}:ingest" for item in app.button)
    next(item for item in cards if item.key.endswith(complete_id)).click().run()
    assert not app.exception
    assert app.session_state["task-center-run:test-space"] == complete_id
    assert any(item.key == f"flow-node:{complete_id}:package" for item in app.button)
    assert any(item.label == "下载本次训练数据与质量证据 ZIP" for item in app.download_button)
    selected_logs = [item.value for item in app.get("html") if isinstance(item.value, str)
                     and '<div class="df-run-log">' in item.value]
    assert selected_logs and "工作流运行结束" in selected_logs[0]
    assert "节点开始运行" not in selected_logs[0]
    assert not any(item.label == "查看数据生成流程" for item in app.selectbox)
    activity = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-task-activity"' in item.value]
    assert activity and "工作流运行结束" in activity[0]


def test_task_status_filter_and_resume_operation(tmp_path):
    source = tmp_path / "guide.txt"
    source.write_text("操作前断电，检查后记录。", encoding="utf-8")
    output = tmp_path / "out"
    completed = create_run(output, sources=[source], targets=["cpt"], name="完成任务")
    Workflow(output, completed, ROOT).execute()
    queued = create_run(output, sources=[source], targets=["cpt"], name="待启动任务")

    app = AppTest.from_function(task_screen, args=(str(output), "filters"), default_timeout=15)
    app.run()
    assert not app.exception
    next(item for item in app.button if item.label == "继续执行 / 从断点重试").click().run()
    assert app.session_state["task_commands"][-1][-1] == queued

    next(item for item in app.segmented_control if item.label == "筛选任务").set_value("已完成").run()
    assert not app.exception
    assert app.session_state["task-center-run:filters"] == completed
    cards = [item for item in app.button if item.key and item.key.startswith("task-card:filters:")]
    assert [item.key for item in cards] == [f"task-card:filters:{completed}"]


def test_empty_task_view_keeps_new_workflow_action(tmp_path):
    app = AppTest.from_function(task_screen, args=(str(tmp_path / "out"), "empty"), default_timeout=15)
    app.run()
    assert not app.exception
    assert any("当前工作区暂无自动工作流" in item.value for item in app.get("html")
               if isinstance(item.value, str))
    assert any("质检与候选打包" in item.value and "人工审核另行发布" in item.value
               for item in app.get("html") if isinstance(item.value, str))
    next(item for item in app.button if item.label == "新建数据工作流").click().run()
    assert app.session_state["new_workflow_requested"] is True


def test_task_progress_includes_implicit_sft_dependency_and_escapes_events():
    from lib.presentation.streamlit.task_management_page import _recent_events_html, _run_card_html

    run = {
        "id": "a" * 32, "status": "queued", "targets": ["dpo"],
        "stages": {"ingest": {"status": "completed"}, "sft": {"status": "completed"},
                   "preference": {"status": "pending"}, "package": {"status": "pending"}},
        "events": [{"at": "2026-09-23T00:00:00+00:00", "stage": "sft",
                    "kind": "stage_completed"},
                   {"at": "2026-09-23T00:01:00+00:00", "stage": "<script>",
                    "kind": "<img src=x onerror=1>"}],
    }
    heading, detail = _run_card_html(run)
    assert 'aria-valuemax="4" aria-valuenow="2"' in detail
    assert "已完成节点 <b>2/4</b>" in detail
    assert "DPO 偏好对" in detail
    assert "待启动" in heading
    activity = _recent_events_html(run)
    assert "08:00 北京时间" in activity
    assert "&lt;script&gt;" in activity and "<script>" not in activity
    assert "&lt;img src=x onerror=1&gt;" in activity


def test_running_graph_uses_actual_edges_and_stage_states_without_horizontal_canvas():
    from lib.presentation.streamlit.workflow_page import _execution_route_html
    from lib.presentation.streamlit.workflow_run_styles import workflow_run_styles

    stages = {"ingest": {"status": "completed"}, "sft": {"status": "completed"},
              "preference": {"status": "running", "done": 2, "total": 5},
              "cot": {"status": "failed"}, "package": {"status": "pending"}}
    route = _execution_route_html(["dpo", "cot"], stages, "preference")

    assert '<div class="df-dag-canvas"' in route and 'aspect-ratio:760/' in route
    assert 'data-from="sft" data-to="preference" data-status="running"' in route
    assert 'data-from="sft" data-to="cot" data-status="attention"' in route
    assert 'data-from="ingest" data-to="sft" data-status="completed"' in route
    assert 'data-stage="sft" data-status="completed"' in route
    assert 'data-intermediate="true"' in route
    assert 'data-stage="cpt"' not in route
    assert 'min-width:760px' not in workflow_run_styles()


def test_run_log_keeps_event_stage_and_escapes_untrusted_details():
    from lib.presentation.streamlit.workflow_page import _events_html

    markup = _events_html([{"at": "2026-09-23T00:00:00+00:00", "stage": "agent",
                            "kind": "stage_completed", "detail": "<script>bad</script>"}])
    assert "Agent 轨迹" in markup and "节点处理完成" in markup
    assert "2026-09-23 00:00:00 UTC" in markup
    assert "&lt;script&gt;bad&lt;/script&gt;" in markup and "<script>" not in markup
