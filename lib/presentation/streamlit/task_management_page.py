"""Selectable task list backed by persisted workflow runs and their events.

The caller owns the page header and the other task-management tabs. This
module renders the data-workflow tab without duplicating workflow execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import re
from typing import Callable

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_graph import execution_graph
from lib.presentation.streamlit.task_management_styles import task_management_styles
from lib.presentation.streamlit.workflow_page import EVENT_LABELS, LABELS, TARGET_LABELS, render_run


FILTERS = ("全部", "未结束", "已完成", "待处理")
FILTER_STATUSES = {
    "未结束": frozenset({"queued", "running"}),
    "已完成": frozenset({"completed"}),
    "待处理": frozenset({"failed", "cancelled", "needs_attention"}),
}
STATUS_GLYPHS = {"queued": "○", "running": "◉", "completed": "✓", "failed": "!",
                 "cancelled": "■", "needs_attention": "◇"}
MARKDOWN_META = re.compile(r"([\\`*_{}\[\]()#+.!|>~-])")
LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def _button_text(value: object) -> str:
    """Keep user-provided task names literal inside Streamlit Markdown labels."""
    return MARKDOWN_META.sub(r"\\\1", str(value).replace("\r", " ").replace("\n", " "))


def _display_time(value: object) -> str:
    """Label persisted UTC timestamps in the operator's local time zone."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            moment = moment.astimezone(LOCAL_TIMEZONE)
            return moment.strftime("%m-%d %H:%M") + " 北京时间"
        return moment.strftime("%m-%d %H:%M")
    except ValueError:
        return str(value or "")[:16].replace("T", " ")


def _stage_progress(run: dict) -> tuple[int, int]:
    """Count completed stages in the run's actual dependency graph."""
    nodes, _ = execution_graph(run.get("targets", []))
    stages = run.get("stages", {})
    return sum(stages.get(key, {}).get("status") == "completed" for key in nodes), len(nodes)


def _run_card_html(run: dict) -> tuple[str, str]:
    status = str(run.get("status", "queued"))
    safe_status = html.escape(status, quote=True)
    done, total = _stage_progress(run)
    percent = round(100 * done / total) if total else 0
    targets = [TARGET_LABELS.get(target, str(target).upper()) for target in run.get("targets", [])]
    tags = "".join(f'<span>{html.escape(label)}</span>' for label in targets[:3])
    if len(targets) > 3:
        tags += f'<span>另有 {len(targets) - 3} 项</span>'
    if not tags:
        tags = '<span>未指定目标</span>'
    moment = html.escape(_display_time(run.get("updated_at") or run.get("created_at")))
    heading = (f'<div class="df-task-card-top"><span class="df-task-status" data-status="{safe_status}">'
               f'<i>{STATUS_GLYPHS.get(status, "?")}</i>{html.escape(LABELS.get(status, status))}</span>'
               f'<time>{moment}</time></div>')
    detail = (f'<div class="df-task-card-targets">{tags}</div>'
              f'<div class="df-task-card-progress"><span>已完成节点 <b>{done}/{total}</b></span>'
              f'<span>#{html.escape(str(run.get("id", ""))[:8])}</span></div>'
              f'<div class="df-task-meter" role="progressbar" aria-label="已完成节点" '
              f'aria-valuemin="0" aria-valuemax="{total}" aria-valuenow="{done}">'
              f'<i style="width:{percent}%"></i></div>')
    return heading, detail


def _recent_events_html(run: dict) -> str:
    """Show run-wide recent events, independent of the selected stage inspector."""
    events = run.get("events", [])[-3:]
    if not events:
        return ('<div class="df-task-activity df-task-activity-empty">'
                '<div class="df-task-activity-head"><strong>运行动态</strong></div>'
                '<span>尚无运行事件；启动后这里会显示实际处理记录。</span></div>')
    stages = run.get("stages", {})
    rows = []
    for event in reversed(events):
        stage_key = str(event.get("stage", ""))
        stage_name = str(stages.get(stage_key, {}).get("label", stage_key or "工作流"))
        kind = str(event.get("kind", "event"))
        label = EVENT_LABELS.get(kind, kind.replace("_", " "))
        moment = _display_time(event.get("at"))
        rows.append('<div class="df-task-activity-event">'
                    f'<i data-kind="{html.escape(kind, quote=True)}"></i>'
                    f'<span><b>{html.escape(stage_name)}</b> · <span>{html.escape(label)}</span></span>'
                    f'<time>{html.escape(moment)}</time></div>')
    return ('<div class="df-task-activity"><div class="df-task-activity-head">'
            f'<strong>运行动态</strong><small>最近 {len(events)} 条</small></div>'
            '<div class="df-task-activity-list">'
            + "".join(rows) + '</div></div>')


def _filtered_runs(runs: list[dict], status_filter: str, search: str) -> list[dict]:
    allowed = FILTER_STATUSES.get(status_filter)
    needle = search.casefold().strip()
    result = []
    for run in runs:
        if allowed is not None and run.get("status") not in allowed:
            continue
        haystack = " ".join([str(run.get("name", "")), str(run.get("id", "")),
                             " ".join(str(target) for target in run.get("targets", []))]).casefold()
        if needle and needle not in haystack:
            continue
        result.append(run)
    return result


def _summary_html(runs: list[dict]) -> str:
    processing = sum(run.get("status") in {"running", "queued"} for run in runs)
    completed = sum(run.get("status") == "completed" for run in runs)
    attention = sum(run.get("status") in {"failed", "cancelled", "needs_attention"} for run in runs)
    cells = (("all", "▤", "全部任务", len(runs)), ("processing", "◉", "待启动 / 执行中", processing),
             ("completed", "✓", "已完成", completed), ("attention", "!", "待处理", attention))
    return '<div class="df-task-summary">' + "".join(
        f'<div class="df-task-summary-item" data-kind="{kind}"><i>{glyph}</i>'
        f'<strong>{number}</strong><span>{label}</span></div>'
        for kind, glyph, label, number in cells) + '</div>'


def _select_run(selection_key: str, run_id: str) -> None:
    st.session_state[selection_key] = run_id


def render_task_management(application: WorkflowApplication, workspace_id: str,
                           begin: Callable[[list[str]], None],
                           on_new_workflow: Callable[[], None]) -> str | None:
    """Render the data-workflow task view and return the selected run ID.

    ``begin`` starts or resumes a persisted workflow, while
    ``on_new_workflow`` navigates to workflow creation. The selected run is
    delegated to ``render_run``, which keeps stop, resume, download and
    artifact-preview operations attached to the real run.
    """
    st.html(task_management_styles())
    if st.session_state.pop("workflow-scroll-top", False):
        # Streamlit keeps the workbench's long-form scroll position across a
        # rerun.  On a fresh handoff, start at this page's header and summary.
        # This script is static and never interpolates workspace/user data.
        st.html("<script>document.querySelector('[data-testid=\"stMain\"]')"
                ".scrollTo({top:0,left:0,behavior:'instant'});</script>",
                unsafe_allow_javascript=True)
    runs = application.list_runs()
    if not runs:
        left, right = st.columns([1.25, 1], gap="large")
        with left, st.container(border=True):
            st.html('<div class="df-task-empty"><span class="df-task-empty-icon">◈</span>'
                    '<h3>当前工作区暂无自动工作流</h3>'
                    '<p>添加文档、Agent 上下文或开放需求后，这里会显示实际运行节点、质量结果与日志。</p></div>')
            st.button("新建数据工作流", type="primary", on_click=on_new_workflow,
                      key=f"task-center-create:{workspace_id}", use_container_width=True)
        with right, st.container(border=True):
            st.html('<div class="df-task-list-head"><strong>任务过程</strong><small>开始后逐步点亮</small></div>'
                    '<div class="df-task-empty-steps">'
                    '<div><b>01</b><span><strong>接入来源</strong><small>文档、对话记录或开放需求</small></span></div>'
                    '<div><b>02</b><span><strong>解析清洗</strong><small>读取、切块与来源追踪</small></span></div>'
                    '<div><b>03</b><span><strong>生成质检</strong><small>按 CPT、SFT、DPO 等目标运行</small></span></div>'
                    '<div><b>04</b><span><strong>质检与候选打包</strong><small>导出可校验候选包；人工审核另行发布</small></span></div>'
                    '</div>')
        return None
    st.html(_summary_html(runs))
    focus = st.toggle("放大工作流视图", key=f"task-center-focus:{workspace_id}",
                      help="展开工作流画布与节点配置；任务列表可从“选择任务”打开。")
    if focus:
        left, right = st.popover("选择任务"), st.container()
    else:
        left, right = st.columns([1.03, 2.7], gap="large")
    with left:
        st.html('<div class="df-task-list-head"><strong>自动工作流</strong>'
                f'<small>共 {len(runs)} 条</small></div>')
        status_filter = st.segmented_control("筛选任务", FILTERS, default="全部",
                                             key=f"task-center-filter:{workspace_id}") or "全部"
        search = st.text_input("搜索任务", placeholder="名称、任务 ID 或训练目标",
                               key=f"task-center-search:{workspace_id}")
        matches = _filtered_runs(runs, status_filter, search)
        visible = matches[:50]
        st.html('<div class="df-task-list-head"><small>按创建时间倒序</small>'
                f'<small>显示 {len(visible)} / 匹配 {len(matches)}</small></div>')
        selection_key = f"task-center-run:{workspace_id}"
        selected_id = st.session_state.get(selection_key)
        visible_ids = {run["id"] for run in visible}
        if selected_id not in visible_ids:
            selected_id = visible[0]["id"] if visible else None
            st.session_state[selection_key] = selected_id
        if not visible:
            st.html('<div class="df-task-no-match">没有符合当前筛选条件的任务。调整状态或搜索词后再查看。</div>')
        with st.container(height=600, border=False):
            for run in visible:
                run_id = str(run["id"])
                status = str(run.get("status", "queued"))
                style_status = "selected" if run_id == selected_id else status
                label = _button_text(run.get("name") or "未命名任务")
                heading, detail = _run_card_html(run)
                with st.container(key=f"task_card_{style_status}_{run_id}"):
                    st.html(heading)
                    st.button(f"**{label}**　↗",
                              key=f"task-card:{workspace_id}:{run_id}", use_container_width=True,
                              on_click=_select_run, args=(selection_key, run_id))
                    st.html(detail)
        st.button("新建数据工作流", on_click=on_new_workflow,
                  key=f"task-center-new:{workspace_id}", use_container_width=True)
    with right:
        if selected_id:
            selected_run = next(run for run in visible if run["id"] == selected_id)
            st.html(_recent_events_html(selected_run))
            render_run(application, selected_id, begin, embedded=True)
        else:
            st.html('<div class="df-task-no-match">选择左侧任务即可查看真实运行节点、配置、日志与产物。</div>')
    return selected_id
