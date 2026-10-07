"""Exercise the standalone task-management view with persisted workflow runs."""
from __future__ import annotations

from pathlib import Path
import json

import pytest
from streamlit.testing.v1 import AppTest

from lib.infrastructure.training_workflow import Workflow, create_run


ROOT = Path(__file__).resolve().parent.parent


def live_canvas(app, run_id):
    return next(json.loads(item.proto.json_args)["spec"] for item in app.get("component_instance")
                if json.loads(item.proto.json_args).get("key") == f"live-canvas:{run_id}")


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
    assert live_canvas(app, queued_id)["selected"] == "ingest"
    next(item for item in cards if item.key.endswith(complete_id)).click().run()
    assert not app.exception
    assert app.session_state["task-center-run:test-space"] == complete_id
    assert live_canvas(app, complete_id)["selected"] == "package"
    assert any(item.label == "查看并打包本次训练数据" for item in app.button)
    selected_logs = [item.value for item in app.get("html") if isinstance(item.value, str)
                     and '<div class="df-run-log">' in item.value]
    assert selected_logs and "工作流运行结束" in selected_logs[0]
    assert "节点开始运行" not in selected_logs[0]
    assert not any(item.label == "查看数据生成流程" for item in app.selectbox)
    activity = [item.value for item in app.get("html") if isinstance(item.value, str)
                and 'class="df-task-activity"' in item.value]
    assert activity and "工作流运行结束" in activity[0]


def test_english_pending_inspector_translates_status_without_changing_task_name(tmp_path):
    source = tmp_path / "guide.txt"
    source.write_text("记录来源。", encoding="utf-8")
    output = tmp_path / "out"
    create_run(output, sources=[source], targets=["cpt"], name="用户任务名称")
    app = AppTest.from_function(task_screen, args=(str(output), "english-pending"), default_timeout=15)
    app.session_state["ui_language"] = "en"
    # The production app installs localization before rendering its pages.
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    install_streamlit_localization()
    app.run()
    assert not app.exception
    head = next(item.value for item in app.get("html")
                if isinstance(item.value, str) and 'class="df-run-inspector-head"' in item.value)
    assert "Pending</span> · 0%" in head and "待处理" not in head
    assert [item.value for item in app.subheader] == ["用户任务名称"]
    assert any(item.label == "**用户任务名称**　↗" for item in app.button)


def test_task_pages_reach_older_runs_and_explicit_location(tmp_path):
    source = tmp_path / "guide.txt"
    source.write_text("操作前断电，检查后记录。", encoding="utf-8")
    output = tmp_path / "out"
    run_ids = {create_run(output, sources=[source], targets=["cpt"], name=f"Task {index}")
               for index in range(53)}
    app = AppTest.from_function(task_screen, args=(str(output), "pages"), default_timeout=15)
    app.run()
    assert not app.exception
    def cards():
        return {item.key.rsplit(":", 1)[1] for item in app.button
                if item.key and item.key.startswith("task-card:pages:")}
    first = cards()
    assert len(first) == 50
    next(item for item in app.button if item.key == "task-center-page:pages:next").click().run()
    assert not app.exception
    second = cards()
    assert len(second) == 3 and not first.intersection(second)
    assert first | second == run_ids
    older = next(iter(second))
    next(item for item in app.button if item.key == "task-center-page:pages:previous").click().run()
    app.session_state["task-center-run:pages"] = older
    app.session_state["task-center-locate:pages"] = older
    app.run()
    assert not app.exception
    assert app.session_state["task-center-page:pages"] == 1
    assert app.session_state["task-center-run:pages"] == older
    next(item for item in app.text_input if item.label == "搜索任务").set_value("no match").run()
    assert not app.exception
    assert app.session_state["task-center-page:pages"] == 0


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
    assert any("本机暂无自动工作流" in item.value for item in app.get("html")
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


def test_running_graph_uses_actual_edges_and_stage_states():
    from lib.presentation.streamlit.workflow_canvas import canvas_spec
    from lib.presentation.streamlit.workflow_page import GRAPH_LABELS, STAGE_GLYPHS

    stages = {"ingest": {"status": "completed"}, "sft": {"status": "completed"},
              "preference": {"status": "running", "done": 2, "total": 5},
              "cot": {"status": "failed"}, "package": {"status": "pending"}}
    route = canvas_spec(["dpo", "cot"], stages, "preference", GRAPH_LABELS, STAGE_GLYPHS, live=True)
    nodes = {node["id"]: node for node in route["nodes"]}
    assert ("sft", "preference") in route["edges"] and ("sft", "cot") in route["edges"]
    assert nodes["sft"]["status"] == "completed" and nodes["sft"]["intermediate"] is True
    assert nodes["preference"]["percent"] == 40 and nodes["cot"]["status"] == "failed"
    assert "cpt" not in nodes and route["selected"] == "preference"


def test_run_log_keeps_event_stage_and_escapes_untrusted_details():
    from lib.presentation.streamlit.workflow_page import _events_html

    markup = _events_html([{"at": "2026-09-23T00:00:00+00:00", "stage": "agent",
                            "kind": "stage_completed", "detail": "<script>bad</script>"}])
    assert "Agent 轨迹" in markup and "节点处理完成" in markup
    assert "2026-09-23 00:00:00 UTC" in markup
    assert "&lt;script&gt;bad&lt;/script&gt;" in markup and "<script>" not in markup


def test_reopened_history_finds_all_independent_runs_by_source_and_year(tmp_path):
    from lib.infrastructure import workflow_task_inventory as inventory
    from lib.infrastructure.training_workflow import atomic_json, read_json, run_path

    source = tmp_path / "Maintenance-Guide.txt"
    source.write_text("Keep each submitted task independent.", encoding="utf-8")
    other_source = tmp_path / "Other-Guide.txt"
    other_source.write_text("A separate source.", encoding="utf-8")
    output = tmp_path / "out"
    ids = [create_run(output, sources=[source], targets=["cpt"], name="Repeated source")
           for _ in range(2)]
    unrelated = create_run(output, sources=[other_source], targets=["cpt"], name="Other source")
    old_state = run_path(output, ids[0]) / "state.json"
    state = read_json(old_state)
    atomic_json(old_state, {**state, "created_at": "2025-01-02T00:00:00+00:00",
                            "updated_at": "2025-01-02T00:00:00+00:00"})
    source.unlink()

    app = AppTest.from_function(task_screen, args=(str(output), "history"), default_timeout=15).run()
    assert not app.exception
    app.text_input(key="task-center-search:history").set_value("maintenance-guide").run()
    assert not app.exception
    cards = {item.key.rsplit(":", 1)[1] for item in app.button
             if item.key and item.key.startswith("task-card:history:")}
    assert cards == set(ids) and unrelated not in cards
    markup = "".join(str(item.value) for item in app.get("html"))
    assert "Maintenance-Guide.txt" in markup and "文档资料" in markup
    assert "2025-01-02 08:00" in markup

    inventory._summary.cache_clear()
    reopened = AppTest.from_function(task_screen, args=(str(output), "history"), default_timeout=15).run()
    assert not reopened.exception
    reopened.text_input(key="task-center-search:history").set_value("maintenance-guide").run()
    reopened_cards = {item.key.rsplit(":", 1)[1] for item in reopened.button
                      if item.key and item.key.startswith("task-card:history:")}
    assert reopened_cards == set(ids)
    reopened.text_input(key="task-center-search:history").set_value("2025-01-02").run()
    assert not reopened.exception
    assert reopened.session_state["task-center-run:history"] == ids[0]
    assert reopened.button(key=f"task-card:history:{ids[0]}")
    assert all((run_path(output, rid) / "inputs/0000.txt").is_file() for rid in ids)


def test_task_source_summary_escapes_file_names_and_brief_content():
    from lib.presentation.streamlit.task_management_page import _run_card_html

    _, detail = _run_card_html({"id": "one", "source_mode": "document",
                                "source_names": ['<img src=x onerror="alert(1)">.txt']})
    assert "<img" not in detail and "&lt;img" in detail
    assert "data-user-content" in detail
    _, brief = _run_card_html({"id": "two", "source_mode": "brief",
                               "source_brief": "<script>private requirement</script>"})
    assert "<script>" not in brief and "&lt;script&gt;" in brief


def test_unreadable_recipe_keeps_history_and_other_tasks_accessible(tmp_path):
    from lib.infrastructure.training_workflow import run_path

    source = tmp_path / "guide.txt"
    source.write_text("A preserved task source.", encoding="utf-8")
    output = tmp_path / "out"
    healthy = create_run(output, sources=[source], targets=["cpt"], name="Healthy task")
    damaged = create_run(output, sources=[source], targets=["cpt"], name="Damaged task")
    recipe_path = run_path(output, damaged) / "recipe.json"
    recipe_path.write_text("{", encoding="utf-8")
    app = AppTest.from_function(task_screen, args=(str(output), "unreadable"), default_timeout=15).run()
    assert not app.exception
    assert app.button(key=f"task-card:unreadable:{damaged}")
    assert app.button(key=f"task-card:unreadable:{healthy}")
    assert any("历史任务仍已保留" in item.value for item in app.warning)
    markup = "".join(str(item.value) for item in app.get("html"))
    assert "来源未知" in markup
    app.button(key=f"task-card:unreadable:{healthy}").click().run()
    assert not app.exception and app.session_state["task-center-run:unreadable"] == healthy
    assert app.button(key=f"task-card:unreadable:{damaged}")


@pytest.mark.parametrize("filename", ["state.json", "recipe.json"])
@pytest.mark.parametrize("changed_content", [None, "{", "{}"])
def test_task_detail_handles_file_changes_after_history_scan(tmp_path, monkeypatch, filename, changed_content):
    from lib.infrastructure.training_workflow import run_path
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver

    source = tmp_path / "guide.txt"
    source.write_text("A persisted source.", encoding="utf-8")
    output = tmp_path / "out"
    other_id = create_run(output, sources=[source], targets=["cpt"], name="Other task")
    run_id = create_run(output, sources=[source], targets=["cpt"], name="Recoverable task")
    changed_path = run_path(output, run_id) / filename
    original_bytes = changed_path.read_bytes()
    inventory = FilesystemWorkflowDriver.task_runs
    changes = []

    def change_after_scan(driver):
        rows = inventory(driver)
        if not changes:
            changes.append(True)
            assert next(row for row in rows if row["id"] == run_id)["recipe_readable"]
            if changed_content is None:
                changed_path.unlink()
            else:
                changed_path.write_text(changed_content, encoding="utf-8")
        return rows

    monkeypatch.setattr(FilesystemWorkflowDriver, "task_runs", change_after_scan)
    app = AppTest.from_function(task_screen, args=(str(output), "race"), default_timeout=15)
    app.session_state["task-center-run:race"] = run_id
    app.run()
    assert changes == [True] and not app.exception
    assert app.button(key=f"task-card:race:{run_id}")
    assert app.button(key=f"task-card:race:{other_id}")
    assert any("历史任务仍已保留" in item.value for item in app.warning)
    assert not any(button.key == f"resume:{run_id}" for button in app.button)
    assert not any(button.key == f"stop:{run_id}" for button in app.button)

    changed_path.write_bytes(original_bytes)
    app.run()
    assert not app.exception and app.session_state["task-center-run:race"] == run_id
    assert any(item.value == "Recoverable task" for item in app.subheader)
    assert app.button(key=f"resume:{run_id}")
