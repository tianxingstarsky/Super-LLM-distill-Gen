"""One-click access to a real task in the selected workspace."""
from __future__ import annotations

import html

import streamlit as st

from lib.presentation.streamlit.i18n import translate


STATUS_LABELS = {
    "running": "处理中",
    "queued": "排队中",
    "needs_attention": "需检查",
    "failed": "失败",
    "completed": "已完成",
    "cancelled": "已停止",
}
STATUS_PRIORITY = {
    "running": 0,
    "needs_attention": 1,
    "queued": 2,
    "failed": 3,
    "completed": 4,
    "cancelled": 5,
}


def _resume_candidate(runs: list[dict]) -> dict | None:
    """Show active work first, then the most recent task needing attention."""
    valid = [run for run in runs if run.get("id")]
    if not valid:
        return None
    priority = min(STATUS_PRIORITY.get(str(run.get("status", "")), 6) for run in valid)
    return max((run for run in valid
                if STATUS_PRIORITY.get(str(run.get("status", "")), 6) == priority),
               key=lambda run: str(run.get("updated_at") or run.get("created_at") or ""))


def _open_task(workspace_id: str, run_id: str) -> None:
    # Widget callbacks run before the next script render. A stale sidebar button
    # must never hand off a task from another workspace.
    if st.session_state.get("ws") != workspace_id:
        return
    st.session_state["nav"] = "任务管理"
    st.session_state[f"task-view:{workspace_id}"] = "数据工作流"
    st.session_state[f"task-center-run:{workspace_id}"] = run_id
    st.session_state[f"task-center-filter:{workspace_id}"] = "全部"
    st.session_state[f"task-center-search:{workspace_id}"] = ""
    st.session_state[f"task-center-locate:{workspace_id}"] = run_id


def render_sidebar_tasks(application, workspace_id: str) -> None:
    """Read only the current workspace's compact task inventory."""
    try:
        candidate = _resume_candidate(application.task_runs())
    except (OSError, ValueError):
        return
    if candidate is None:
        return
    run_id = str(candidate["id"])
    language = st.session_state.get("ui_language", "zh")
    status = translate(STATUS_LABELS.get(str(candidate.get("status")), "需检查"), language)
    name = str(candidate.get("name") or candidate["id"]).replace("\r", " ").replace("\n", " ")
    with st.sidebar.container(border=True):
        st.caption("最近任务")
        st.html('<div class="df-sidebar-recent-task"><strong data-user-content>'
                + html.escape(name) + '</strong><small>' + html.escape(status)
                + '</small></div>')
        st.button("查看 →", key=f"sidebar-task:{workspace_id}",
                  on_click=_open_task, args=(workspace_id, run_id), width="stretch")
