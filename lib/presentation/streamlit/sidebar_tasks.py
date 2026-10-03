"""Compact shortcuts for concurrent tasks in the selected workspace."""
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
SHORTCUT_LIMIT = 3


def _resume_candidate(runs: list[dict]) -> dict | None:
    """Show active work first, then the most recent task needing attention."""
    valid = [run for run in runs if run.get("id")]
    if not valid:
        return None
    priority = min(STATUS_PRIORITY.get(str(run.get("status", "")), 6) for run in valid)
    return max((run for run in valid
                if STATUS_PRIORITY.get(str(run.get("status", "")), 6) == priority),
               key=lambda run: str(run.get("updated_at") or run.get("created_at") or ""))


def _shortcut_runs(runs: list[dict], limit: int = SHORTCUT_LIMIT) -> list[dict]:
    """Keep live runs easy to switch while reserving room for attention."""
    if limit <= 0:
        return []
    unique: dict[str, dict] = {}
    for run in runs:
        run_id = str(run.get("id") or "")
        if run_id:
            unique[run_id] = run
    ordered = sorted(unique.values(),
                     key=lambda run: str(run.get("updated_at") or run.get("created_at") or ""),
                     reverse=True)
    ordered.sort(key=lambda run: STATUS_PRIORITY.get(str(run.get("status", "")), 6))
    actionable = [run for run in ordered if run.get("status") in
                  {"running", "queued", "needs_attention", "failed"}]
    if not actionable:
        return ordered[:1]
    selected = actionable[:limit]
    attention = next((run for run in actionable if run.get("status") in
                      {"needs_attention", "failed"}), None)
    if attention and all(run["id"] != attention["id"] for run in selected):
        selected[-1] = attention
        selected.sort(key=lambda run: STATUS_PRIORITY.get(str(run.get("status", "")), 6))
    return selected


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
        runs = application.task_runs()
        shortcuts = _shortcut_runs(runs)
    except (OSError, ValueError):
        return
    if not shortcuts:
        return
    language = st.session_state.get("ui_language", "zh")
    with st.sidebar.container(border=True):
        heading = ("进行中与待处理" if any(run.get("status") in
                   {"running", "queued", "needs_attention", "failed"} for run in shortcuts)
                   else "最近任务")
        st.caption(translate(heading, language))
        for candidate in shortcuts:
            run_id = str(candidate["id"])
            status = translate(STATUS_LABELS.get(str(candidate.get("status")), "需检查"), language)
            name = str(candidate.get("name") or run_id).replace("\r", " ").replace("\n", " ")
            st.html('<div class="df-sidebar-recent-task"><strong data-user-content>'
                    + html.escape(name) + '</strong><small>' + html.escape(status)
                    + '</small></div>')
            st.button("查看 →", key=f"sidebar-task:{workspace_id}:{run_id}",
                      on_click=_open_task, args=(workspace_id, run_id), width="stretch")
        remaining = len({str(run.get("id")) for run in runs if run.get("id")}) - len(shortcuts)
        if remaining > 0:
            st.caption(translate("另有 {count} 条任务可在任务管理中查看", language).format(count=remaining))
