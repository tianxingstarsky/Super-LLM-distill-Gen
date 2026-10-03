"""Concurrent workflow tasks remain easy to find in the current workspace."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.task_management_page import _quick_switch_runs


def test_quick_switch_shows_recent_distinct_actionable_runs_only():
    runs = [
        {"id": f"running-{number}", "status": "running",
         "updated_at": f"2026-10-03T{number:02d}:00:00"} for number in range(8)
    ] + [
        {"id": f"attention-{number}", "status": "needs_attention",
         "updated_at": f"2026-10-03T{number:02d}:30:00"} for number in range(5)
    ] + [
        {"id": "running-7", "status": "running", "updated_at": "2026-10-03T07:00:00"},
        {"id": "done", "status": "completed", "updated_at": "2026-10-03T23:00:00"},
    ]
    attention, active = _quick_switch_runs(runs)
    assert attention[1] == 5 and active[1] == 8
    assert [row["id"] for row in attention[0]] == ["attention-4", "attention-3", "attention-2"]
    assert [row["id"] for row in active[0]] == ["running-7", "running-6", "running-5"]


def test_shortcut_clears_filters_and_locates_older_active_run_on_later_page():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        completed = [{"id": f"done-{number:03d}", "name": f"Finished {number}",
                      "status": "completed", "created_at": "2026-10-03T12:00:00",
                      "updated_at": "2026-10-03T12:00:00", "targets": ["sft"]}
                     for number in range(120)]
        return completed + [
            {"id": "old-running", "name": "Older active request", "status": "running",
             "created_at": "2026-09-30T12:00:00", "updated_at": "2026-10-03T13:00:00",
             "targets": ["sft"]},
            {"id": "attention", "name": "Review this request", "status": "needs_attention",
             "created_at": "2026-09-29T12:00:00", "updated_at": "2026-10-03T14:00:00",
             "targets": ["cpt"]},
        ]

st.session_state["ws"] = "fixture"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(f"DETAIL {run_id}")):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    assert ui.button(key="task-quick:fixture:old-running")
    ui.segmented_control(key="task-center-filter:fixture").set_value("已完成").run()
    ui.text_input(key="task-center-search:fixture").set_value("Finished").run()
    assert ui.session_state["task-center-run:fixture"] != "old-running"

    ui.button(key="task-quick:fixture:old-running").click().run()
    assert not ui.exception
    assert ui.session_state["task-center-filter:fixture"] == "全部"
    assert ui.session_state["task-center-search:fixture"] == ""
    assert ui.session_state["task-center-page:fixture"] == 2
    assert ui.session_state["task-center-run:fixture"] == "old-running"
    assert ui.number_input(key="task-center-page:fixture:jump").value == 3


def test_page_number_jump_opens_distant_tasks_and_tracks_navigation():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        return [{"id": f"run-{number:05d}", "name": f"Task {number:05d}",
                 "status": "completed", "created_at": "2026-10-03T12:00:00",
                 "updated_at": "2026-10-03T12:00:00", "targets": ["sft"]}
                for number in range(10_051)]

st.session_state["ws"] = "fixture"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(f"DETAIL {run_id}")):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception

    jump = ui.number_input(key="task-center-page:fixture:jump")
    assert jump.value == 1
    jump.set_value(202).run()
    assert not ui.exception
    assert ui.session_state["task-center-page:fixture"] == 201
    assert ui.session_state["task-center-run:fixture"] == "run-10050"
    assert ui.button(key="task-card:fixture:run-10050")

    ui.button(key="task-center-page:fixture:previous").click().run()
    assert not ui.exception
    assert ui.number_input(key="task-center-page:fixture:jump").value == 201
    assert ui.session_state["task-center-run:fixture"] == "run-10000"

    ui.text_input(key="task-center-search:fixture").set_value("Task 10050").run()
    assert not ui.exception
    assert ui.session_state["task-center-page:fixture"] == 0
    assert ui.session_state["task-center-run:fixture"] == "run-10050"
    assert not ui.get("number_input")


def test_workspace_change_never_shows_other_workspaces_shortcuts():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def __init__(self, workspace): self.workspace = workspace
    def task_runs(self):
        return [{"id": self.workspace + "-run", "name": self.workspace + " private task",
                 "status": "running", "created_at": "2026-10-03T12:00:00",
                 "updated_at": "2026-10-03T12:00:00", "targets": ["sft"]}]

workspace = st.selectbox("Workspace", ["alpha", "beta"], key="ws")
st.button("Stale shortcut", key="stale-shortcut",
          on_click=page._switch_run, args=("alpha", "alpha-other"))
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(run_id)):
    page.render_task_management(Application(workspace), workspace, lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    assert ui.button(key="task-quick:alpha:alpha-run")
    ui.selectbox(key="ws").select("beta").run()
    assert not ui.exception
    assert ui.button(key="task-quick:beta:beta-run")
    assert not [button for button in ui.button if button.key.startswith("task-quick:alpha:")]
    assert ui.session_state["task-center-run:beta"] == "beta-run"
    ui.button(key="stale-shortcut").click().run()
    assert not ui.exception
    assert ui.session_state["task-center-run:alpha"] == "alpha-run"
    assert ui.session_state["task-center-run:beta"] == "beta-run"


def test_shortcut_translates_interface_without_rewriting_task_name():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        return [{"id": "one", "name": "客户任务 Keep 原文", "status": "running",
                 "created_at": "2026-10-03T12:00:00", "updated_at": "2026-10-03T12:00:00",
                 "targets": ["sft"]}]

st.session_state["ws"] = "fixture"
st.session_state["ui_language"] = "en"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(run_id)):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    markup = "".join(item.proto.body for item in ui.get("html"))
    assert "Concurrent tasks" in markup and "Unfinished" in markup
    shortcut = ui.button(key="task-quick:fixture:one")
    assert "客户任务 Keep 原文" in shortcut.label
    assert "Running" in shortcut.label
