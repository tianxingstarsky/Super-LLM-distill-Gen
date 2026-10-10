"""Selectable task list backed by persisted workflow runs and their events.

The caller owns the page header and the other task-management tabs. This
module renders the data-workflow tab without duplicating workflow execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from heapq import nlargest
import html
import re
from typing import Callable

import streamlit as st
from lib.domain.workflow_delivery import has_deliverable_results

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_graph import execution_graph
from lib.presentation.streamlit.task_management_styles import task_management_styles
from lib.presentation.streamlit.i18n import UntranslatedText, translate
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
QUICK_SWITCH_LIMIT = 3
ACTIVE_STATUSES = frozenset({"queued", "running"})
ATTENTION_STATUSES = frozenset({"failed", "cancelled", "needs_attention"})
TARGET_SHORT_LABELS = {
    "cpt": "CPT", "sft": "SFT", "dpo": "DPO", "cot": "CoT", "orpo": "ORPO",
    "rlaif": "RLAIF", "agent": "Agent", "multiturn": "Multi-turn", "gsm8k": "GSM8K",
}


def _button_text(value: object) -> str:
    """Keep user-provided task names literal inside Streamlit Markdown labels."""
    return MARKDOWN_META.sub(r"\\\1", str(value).replace("\r", " ").replace("\n", " "))


def _display_time(value: object) -> str:
    """Label persisted UTC timestamps in the operator's local time zone."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            moment = moment.astimezone(LOCAL_TIMEZONE)
            return moment.strftime("%Y-%m-%d %H:%M") + " 北京时间"
        return moment.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value or "")[:16].replace("T", " ")


def _stage_progress(run: dict) -> tuple[int, int]:
    """Count completed stages in the run's actual dependency graph."""
    stages = run.get("stages", {})
    # Older runs also carry unused stage keys. Only a scheduled trim stage
    # contributes to progress; its recipe flag is copied into new run state.
    trimming = bool(run.get("reasoning_trim_enabled")) or stages.get("trim", {}).get("status") in {
        "running", "completed", "failed", "cancelled"}
    nodes, _ = execution_graph(run.get("targets", []), reasoning_trim=trimming,
                                qa_director={"enabled": bool(run.get("qa_director_enabled"))},
                                package_review={"enabled": "jev" in stages, "node": "jev"})
    return sum(stages.get(key, {}).get("status") == "completed" for key in nodes), len(nodes)


def _run_card_html(run: dict, *, language: str = "zh") -> tuple[str, str]:
    status = str(run.get("status", "queued"))
    safe_status = html.escape(status, quote=True)
    done, total = _stage_progress(run)
    percent = round(100 * done / total) if total else 0
    progress_label = "已完成节点"
    goals = (run.get("production") or {}).get("goals", {})
    soft = (run.get("production") or {}).get("version", 1) >= 2
    if goals and not soft:
        total = sum(goal["goal"] for goal in goals.values())
        done = sum(min(goal["goal"], goal.get("eligible", 0)) for goal in goals.values())
        percent = round(100 * done / total) if total else 0
        progress_label = "合格数量"
    count_text = f'{progress_label} <b>{done:,}/{total:,}</b>'
    output = ""
    if goals and soft:
        actual = sum(goal.get("eligible", 0) for goal in goals.values())
        expected = sum(goal["goal"] for goal in goals.values())
        output = ('<div class="df-task-card-output"><span>产出 / 期望</span>'
                  f'<b>{actual:,}/{expected:,}</b></div>')
    targets = list(run.get("targets", []))
    full_labels = [translate(TARGET_LABELS.get(target, str(target).upper()), language)
                   for target in targets]
    # Short types scan on one line. Both the tooltip and the accessible text
    # retain the complete training-target meaning in the active UI language.
    tags = "".join(
        f'<span title="{html.escape(full_label, quote=True)}">'
        f'<span aria-hidden="true">{html.escape(TARGET_SHORT_LABELS.get(target, str(target).upper()))}</span>'
        f'<span class="df-task-sr-only">{html.escape(full_label)}</span></span>'
        for target, full_label in zip(targets[:3], full_labels[:3])
    )
    if len(targets) > 3:
        remaining = html.escape(" · ".join(full_labels[3:]), quote=True)
        tags += (f'<span title="{remaining}"><span aria-hidden="true">+{len(targets) - 3}</span>'
                 f'<span class="df-task-sr-only">{remaining}</span></span>')
    if not tags:
        tags = '<span>未指定目标</span>'
    moment = _display_time(run.get("updated_at") or run.get("created_at"))
    compact_moment = html.escape(moment.replace(" 北京时间", ""))
    heading = (f'<div class="df-task-card-top"><span class="df-task-status" data-status="{safe_status}">'
               f'<i>{STATUS_GLYPHS.get(status, "?")}</i>{html.escape(LABELS.get(status, status))}</span>'
               f'<time title="{html.escape(moment, quote=True)}">{compact_moment}</time></div>')
    source_modes = {"document": "文档资料", "agent": "Agent 上下文", "brief": "开放需求"}
    source_label = source_modes.get(run.get("source_mode"), "来源未知")
    source_names = run.get("source_names", [])
    source_text = "、".join(str(name) for name in source_names[:2])
    if len(source_names) > 2:
        source_text += f" · +{len(source_names) - 2}"
    if not source_text:
        source_text = str(run.get("source_brief") or "")
    source_title = html.escape(source_text, quote=True)
    detail = (f'<div class="df-task-card-targets">{tags}</div>'
              f'<div class="df-task-card-source"><b>{source_label}</b>'
              f'<span data-user-content title="{source_title}">{html.escape(source_text)}</span></div>'
              f'{output}'
              f'<div class="df-task-card-progress"><span>{count_text}</span>'
              f'<span>#{html.escape(str(run.get("id", ""))[:8])}</span></div>'
              f'<div class="df-task-meter" data-status="{safe_status}" role="progressbar" aria-label="{progress_label}" '
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
                             " ".join(str(target) for target in run.get("targets", [])),
                             " ".join(str(name) for name in run.get("source_names", [])),
                             str(run.get("source_brief", "")),
                             _display_time(run.get("created_at")),
                             _display_time(run.get("updated_at"))]).casefold()
        if needle and needle not in haystack:
            continue
        result.append(run)
    return result


def _summary_html(runs: list[dict]) -> str:
    processing = sum(run.get("status") in {"running", "queued"} for run in runs)
    completed = sum(run.get("status") == "completed" for run in runs)
    attention = sum(run.get("status") in {"failed", "cancelled", "needs_attention"} for run in runs)
    cells = (("all", "全部任务", len(runs)), ("processing", "待启动 / 执行中", processing),
             ("completed", "已完成", completed), ("attention", "待处理", attention))
    return '<div class="df-task-summary">' + "".join(
        f'<div class="df-task-summary-item" data-kind="{kind}">'
        f'<span>{label}</span><strong>{number:,}</strong></div>'
        for kind, label, number in cells) + '</div>'


def _quick_switch_runs(runs: list[dict], limit: int = QUICK_SWITCH_LIMIT
                       ) -> tuple[tuple[list[dict], int], tuple[list[dict], int]]:
    """Pick a bounded, recent sample of actionable runs from this inventory."""
    unique: dict[str, dict] = {}
    for run in runs:
        run_id = str(run.get("id") or "")
        if not run_id:
            continue
        previous = unique.get(run_id)
        if previous is None or str(run.get("updated_at") or "") > str(previous.get("updated_at") or ""):
            unique[run_id] = run

    def newest(rows: list[dict]) -> list[dict]:
        return nlargest(max(0, limit), rows,
                        key=lambda run: (str(run.get("updated_at") or run.get("created_at") or ""),
                                         str(run.get("created_at") or ""), str(run["id"])))

    attention = [run for run in unique.values() if run.get("status") in ATTENTION_STATUSES]
    active = [run for run in unique.values() if run.get("status") in ACTIVE_STATUSES]
    return (newest(attention), len(attention)), (newest(active), len(active))


def _select_run(selection_key: str, run_id: str) -> None:
    st.session_state[selection_key] = run_id


def _switch_run(workspace_id: str, run_id: str) -> None:
    """Locate a shortcut's exact run without changing pages or workspaces."""
    if st.session_state.get("ws", workspace_id) != workspace_id:
        return
    st.session_state[f"task-center-run:{workspace_id}"] = run_id
    st.session_state[f"task-center-filter:{workspace_id}"] = "全部"
    st.session_state[f"task-center-search:{workspace_id}"] = ""
    st.session_state[f"task-center-locate:{workspace_id}"] = run_id


def _show_quick_group(workspace_id: str, status_filter: str) -> None:
    """Expose the complete actionable group in this page's task list."""
    if (st.session_state.get("ws", workspace_id) != workspace_id
            or status_filter not in {"未结束", "待处理"}):
        return
    st.session_state[f"task-center-filter:{workspace_id}"] = status_filter
    st.session_state[f"task-center-search:{workspace_id}"] = ""
    st.session_state[f"task-center-page:{workspace_id}"] = 0
    st.session_state[f"task-center-focus:{workspace_id}"] = False
    st.session_state.pop(f"task-center-locate:{workspace_id}", None)


def _quick_choice(workspace_id: str, key: str) -> None:
    selected = st.session_state.get(key)
    if selected == "group:attention":
        _show_quick_group(workspace_id, "待处理")
    elif selected == "group:active":
        _show_quick_group(workspace_id, "未结束")
    elif selected:
        _switch_run(workspace_id, selected)
    st.session_state[key] = None


def _change_page(page_key: str, page: int) -> None:
    st.session_state[page_key] = page


def _jump_to_page(page_key: str, jump_key: str) -> None:
    """Convert the operator's one-based page number to the list's offset."""
    st.session_state[page_key] = int(st.session_state[jump_key]) - 1


def render_task_management(application: WorkflowApplication, workspace_id: str,
                           begin: Callable[[list[str]], None],
                           on_new_workflow: Callable[[], None], *, draft_application=None, backend_application=None) -> str | None:
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
    runs = application.task_runs()
    if not runs:
        with st.container(key="task-center-empty"):
            left, right = st.columns([1.25, 1], gap="large")
            with left:
                st.html('<div class="df-task-empty"><span class="df-task-empty-icon">◈</span>'
                        '<h3>本机暂无自动工作流</h3>'
                        '<p>添加文档、Agent 上下文或开放需求后，这里会显示实际运行节点、质量结果与日志。</p></div>')
                st.button("新建数据工作流", type="primary", on_click=on_new_workflow,
                          key=f"task-center-create:{workspace_id}", use_container_width=True)
            with right:
                st.html('<div class="df-task-list-head"><strong>任务过程</strong><small>开始后逐步点亮</small></div>'
                        '<div class="df-task-empty-steps">'
                        '<div><b>01</b><span><strong>接入来源</strong><small>文档、对话记录或开放需求</small></span></div>'
                        '<div><b>02</b><span><strong>解析清洗</strong><small>读取、切块与来源追踪</small></span></div>'
                        '<div><b>03</b><span><strong>生成质检</strong><small>按 CPT、SFT、DPO 等目标运行</small></span></div>'
                        '<div><b>04</b><span><strong>质检与候选打包</strong><small>导出可校验候选包；人工审核另行发布</small></span></div>'
                        '</div>')
        return None
    attention, active = _quick_switch_runs(runs)
    focus_key = f"task-center-focus:{workspace_id}"
    show_quick_switch = bool((attention[1] or active[1]) and len(runs) > 3)
    with st.container(key="task-center-toolbar"):
        toolbar = st.columns([4.3, 1.8, 2.1, 1.65] if show_quick_switch else [4.3, 1.8, 1.65],
                             gap="small", vertical_alignment="center")
    with toolbar[0]:
        st.html(_summary_html(runs))
    focus_column, create_column = toolbar[1], toolbar[-1]
    with focus_column:
        focus = st.toggle("放大工作流视图", value=False, key=focus_key,
                          help="展开工作流画布与节点配置；任务列表可从“选择任务”打开。")
    if show_quick_switch:
        language = st.session_state.get("ui_language", "zh")
        shortcuts = {str(run["id"]): run for run in [*attention[0], *active[0]]}
        options = [None, *shortcuts]
        if attention[1] > len(attention[0]):
            options.append("group:attention")
        if active[1] > len(active[0]):
            options.append("group:active")

        def quick_label(identifier):
            if identifier is None:
                return translate("选择任务", language)
            if identifier in {"group:attention", "group:active"}:
                return translate("查看全部待处理任务" if identifier == "group:attention"
                                 else "查看全部未结束任务", language)
            run = shortcuts[identifier]
            name = str(run.get("name") or "未命名任务").replace("\r", " ").replace("\n", " ")
            status = translate(LABELS.get(str(run.get("status", "")), "待处理"), language)
            return UntranslatedText(f"{name} · {status}")

        quick_key = f"task-quick-select:{workspace_id}"
        if st.session_state.get(quick_key) not in options:
            st.session_state[quick_key] = None
        with toolbar[2]:
            st.selectbox(translate("切换任务", language), options, key=quick_key, format_func=quick_label,
                         label_visibility="collapsed", on_change=_quick_choice,
                         args=(workspace_id, quick_key),
                         help="直接切换正在运行或待处理的任务")
    with create_column:
        st.button("新建数据工作流", on_click=on_new_workflow,
                  key=f"task-center-new:{workspace_id}", use_container_width=True)
    with st.container(key="task-center-body"):
        if focus:
            left, right = st.popover("选择任务"), st.container()
        else:
            left, right = st.columns([1, 2.75], gap="medium")
        with left, st.container(key=f"task-center-list-{workspace_id}"):
            st.html('<div class="df-task-list-head"><strong>自动工作流</strong>'
                    f'<small>共 {len(runs)} 条</small></div>')
            status_filter = st.segmented_control("筛选任务", FILTERS, default="全部",
                                                 key=f"task-center-filter:{workspace_id}",
                                                 label_visibility="collapsed", width="stretch") or "全部"
            search = st.text_input("搜索任务", placeholder="任务名称、来源文件、日期或 ID",
                                   key=f"task-center-search:{workspace_id}", label_visibility="collapsed")
            matches = _filtered_runs(runs, status_filter, search)
            page_key = f"task-center-page:{workspace_id}"
            filter_key = page_key + ":filter"
            signature = (status_filter, search)
            page = st.session_state.get(page_key, 0)
            if st.session_state.get(filter_key) != signature:
                page = 0
            st.session_state[filter_key] = signature
            locate = st.session_state.pop(f"task-center-locate:{workspace_id}", None)
            if locate:
                page = next((index // 50 for index, run in enumerate(matches) if run["id"] == locate), 0)
            page_count = max(1, (len(matches) + 49) // 50)
            page = min(max(0, page), page_count - 1)
            st.session_state[page_key] = page
            visible = matches[page * 50:(page + 1) * 50]
            if page_count > 1:
                jump_key = page_key + ":jump"
                # Keep the editor aligned after filtering, shortcut selection, and
                # Previous/Next callbacks. This runs before the widget is created.
                st.session_state[jump_key] = page + 1
                st.number_input("跳转页码", min_value=1, max_value=page_count, step=1,
                                key=jump_key, on_change=_jump_to_page,
                                args=(page_key, jump_key))
                previous, following = st.columns(2)
                previous.button("上一页任务", disabled=page == 0, key=page_key + ":previous",
                                on_click=_change_page, args=(page_key, page - 1), width="stretch")
                following.button("下一页任务", disabled=page == page_count - 1, key=page_key + ":next",
                                 on_click=_change_page, args=(page_key, page + 1), width="stretch")
                st.caption(f"{page + 1} / {page_count}")
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
                return None
            with st.container(height=600 if len(visible) > 3 else "content", border=False):
                for run in visible:
                    run_id = str(run["id"])
                    status = str(run.get("status", "queued"))
                    style_status = "selected" if run_id == selected_id else status
                    label = _button_text(run.get("name") or "未命名任务")
                    heading, detail = _run_card_html(run, language=st.session_state.get("ui_language", "zh"))
                    with st.container(key=f"task_card_{style_status}_{run_id}"):
                        st.html(heading)
                        st.button(UntranslatedText(f"**{label}**　→"),
                                  key=f"task-card:{workspace_id}:{run_id}", use_container_width=True,
                                  on_click=_select_run, args=(selection_key, run_id))
                        st.html(detail)
        with right:
            if selected_id:
                selected_run = next(run for run in visible if run["id"] == selected_id)
                if ("agent" in selected_run.get("targets", []) and
                        has_deliverable_results(selected_run)):
                    review_key = f"task-center-agent-review:{workspace_id}:{selected_id}"
                    if st.toggle("审查本任务 Agent 轨迹", key=review_key,
                                 help="直接在当前任务核对工具过程与重放证据，处理正样本并查看失败轨迹。"):
                        from lib.presentation.streamlit.agent_review_page import render_agent_review

                        render_agent_review(application, selected_id, workspace_id=workspace_id)
                if selected_run.get("recipe_readable", True):
                    render_run(application, selected_id, begin, embedded=True,
                               **({"backend_application": backend_application} if backend_application is not None else {}),
                               **({"draft_application": draft_application} if draft_application is not None else {}))
                else:
                    st.warning("历史任务仍已保留，暂时无法读取完整运行记录。请检查任务文件。")
    return selected_id
