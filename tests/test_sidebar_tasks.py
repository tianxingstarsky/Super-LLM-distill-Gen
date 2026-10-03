"""Task shortcuts must stay inside the selected workspace."""
from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.sidebar_tasks import _resume_candidate, _shortcut_runs


def test_resume_prefers_live_work_and_latest_update():
    runs = [
        {"id": "done", "status": "completed", "updated_at": "2026-10-03T12:00:00"},
        {"id": "older", "status": "running", "updated_at": "2026-10-02T12:00:00"},
        {"id": "newer", "status": "running", "updated_at": "2026-10-03T11:00:00"},
    ]
    assert _resume_candidate(runs)["id"] == "newer"


def test_shortcuts_reserve_attention_among_concurrent_runs():
    runs = [
        {"id": "failed", "status": "failed", "updated_at": "2026-10-01T11:00:00"},
        *({"id": f"running-{number}", "status": "running",
           "updated_at": f"2026-10-03T{number:02d}:00:00"} for number in range(4)),
        {"id": "done", "status": "completed", "updated_at": "2026-10-03T23:00:00"},
    ]
    assert [run["id"] for run in _shortcut_runs(runs)] == ["running-3", "running-2", "failed"]
    assert [run["id"] for run in _shortcut_runs([runs[-1]])] == ["done"]


def test_sidebar_opens_exact_task_and_does_not_offer_other_workspace_task():
    script = '''
import streamlit as st
from lib.presentation.streamlit.sidebar_tasks import render_sidebar_tasks

class Application:
    def __init__(self, ws): self.ws = ws
    def task_runs(self):
        if self.ws == "alpha":
            return [{"id": "run-alpha", "name": "First task", "status": "running",
                     "updated_at": "2026-10-03T11:00:00"},
                    {"id": "run-second", "name": "Second task", "status": "running",
                     "updated_at": "2026-10-03T12:00:00"}]
        return []

st.selectbox("Workspace", ["alpha", "beta"], key="ws")
render_sidebar_tasks(Application(st.session_state["ws"]), st.session_state["ws"])
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert ui.sidebar.button(key="sidebar-task:alpha:run-alpha")
    assert ui.sidebar.button(key="sidebar-task:alpha:run-second")
    ui.sidebar.button(key="sidebar-task:alpha:run-alpha").click().run()
    assert ui.session_state["nav"] == "任务管理"
    assert ui.session_state["task-center-run:alpha"] == "run-alpha"
    assert ui.session_state["task-center-locate:alpha"] == "run-alpha"
    ui.selectbox(key="ws").select("beta").run()
    assert not ui.exception
    assert not [button for button in ui.sidebar.button if button.key.startswith("sidebar-task:beta:")]
    assert "task-center-run:beta" not in ui.session_state
