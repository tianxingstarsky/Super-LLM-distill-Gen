"""A welcoming overview of local work, with direct creation and review shortcuts."""
import html
import base64

import streamlit as st

from lib.application.workflow_service import WorkflowApplication
from lib.presentation.streamlit.home_style import HOME_STYLE
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.i18n import UntranslatedText, translate
from lib.presentation.streamlit.workflow_page import TARGET_LABELS


# Source illustrations are decorative. Native buttons remain the accessible,
# keyboard-operable entry points and keep their existing navigation callbacks.
_SOURCE_ART = {
    "documents": '<path d="M15 13h25l10 10v35H15z" fill="currentColor" opacity=".14"/>'
                 '<path d="M10 7h25l10 10v35H10z" fill="white" stroke="currentColor" stroke-width="2"/>'
                 '<path d="M35 7v11h10M18 27h19M18 34h19M18 41h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
                 '<path d="m43 38 10 6v12l-10 6-10-6V44z" fill="currentColor"/>'
                 '<path d="m33 44 10 6 10-6m-10 6v12" fill="none" stroke="white" stroke-opacity=".8" stroke-width="1.5"/>',
    "agent": '<path d="M14 16h34a8 8 0 0 1 8 8v20a8 8 0 0 1-8 8H31L19 60v-8h-5a8 8 0 0 1-8-8V24a8 8 0 0 1 8-8" fill="currentColor" opacity=".13"/>'
             '<rect x="12" y="14" width="38" height="31" rx="9" fill="white" stroke="currentColor" stroke-width="2"/>'
             '<path d="M31 14V7m-5 30h10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
             '<circle cx="31" cy="6" r="3" fill="currentColor"/>'
             '<circle cx="23" cy="27" r="3" fill="currentColor"/><circle cx="39" cy="27" r="3" fill="currentColor"/>'
             '<path d="m46 43 9 5v10l-9 5-9-5V48z" fill="currentColor"/>'
             '<path d="m43 50-3 3 3 3m6-6 3 3-3 3" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    "brief": '<path d="m31 7 23 13v27L31 60 8 47V20z" fill="currentColor" opacity=".12"/>'
             '<path d="m31 10 20 11v23L31 55 11 44V21z" fill="white" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>'
             '<path d="m11 21 20 11 20-11M31 32v23" fill="none" stroke="currentColor" stroke-width="2"/>'
             '<path d="m31 17 3 6 7 2-7 2-3 6-3-6-7-2 7-2z" fill="currentColor"/>'
             '<path d="m52 5 2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="currentColor" opacity=".6"/>',
}
_SOURCE_COLORS = {"documents": "#236BC7", "agent": "#148368", "brief": "#8254C3"}
# Streamlit sanitizes inline SVG from st.html. An image data URI preserves the
# small vector artwork without adding a request or executable page content.
_SOURCE_ART = {
    kind: "data:image/svg+xml;base64," + base64.b64encode(
        ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 68" '
         f'color="{_SOURCE_COLORS[kind]}">' + paths + '</svg>').encode()).decode()
    for kind, paths in _SOURCE_ART.items()
}


def render_overview(application: WorkflowApplication, ws, inventory, source_total, sample_count,
                    source_location, output_location, *, navigate, job_status, open_folder=None):
    page_header("首页", "从文档、智能体上下文和开放需求，构建可追溯的高质量训练数据。", variant="hub")
    st.html(HOME_STYLE)

    def start_mode(mode: str, preset: str) -> None:
        st.session_state[f'workflow-source-mode:{ws}'] = mode
        st.session_state[f'workflow-preset:{ws}'] = preset
        st.session_state[f'workflow-creation-mode:{ws}'] = '自动生成'
        st.session_state['nav'] = '自动工作流'

    def start_target(target: str) -> None:
        st.session_state[f'workflow-entry-target:{ws}'] = target
        st.session_state[f'workflow-creation-mode:{ws}'] = '自动生成'
        st.session_state['nav'] = '自动工作流'

    def start_manual() -> None:
        st.session_state[f'workflow-creation-mode:{ws}'] = '人工制作图文'
        st.session_state['nav'] = '自动工作流'

    def open_run(run_id: str) -> None:
        st.session_state[f'task-view:{ws}'] = '数据工作流'
        st.session_state[f'task-center-run:{ws}'] = run_id
        st.session_state[f'workflow-selected:{ws}'] = run_id
        st.session_state['nav'] = '任务管理'

    try:
        runs = application.task_runs()
    except OSError:
        runs = []
    recent = sorted(runs, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
                    reverse=True)[:5]
    items = (("工作流总数", len(runs), "blue"),
             ("已完成", sum(row.get("status") == "completed" for row in runs), "green"),
             ("处理中", sum(row.get("status") in {"running", "queued"} for row in runs), "blue"),
             ("需检查", sum(row.get("status") in {"failed", "needs_attention"} for row in runs), "amber"))
    st.html('<div class="df-home-kpis">' + ''.join(
        f'<div class="df-home-kpi" data-tone="{tone}"><small>{label}</small><strong>{value}</strong></div>'
        for label, value, tone in items) + '</div>')

    with st.container(key="home-main"):
        source_column, recent_column = st.columns([1.15, 1], gap="small")
        with source_column:
            with st.container(border=True, key="home-source-panel"):
                section_heading("开始制作数据", "导入资料，或直接选择要制作的数据类型。", "◈")
                with st.container(key="home-source-entries"):
                    cards = st.columns(3, gap="small")
                    entries = (
                        ("文档资料", "MD / TXT / PDF / DOCX", "自动推荐", "documents"),
                        ("Agent 上下文", "导入对话、工具调用和观测。", "Agent 轨迹", "agent"),
                        ("开放需求", "描述场景，生成多样化候选。", "自动推荐", "brief"),
                    )
                    for column, (mode, detail, preset, kind) in zip(cards, entries):
                        with column, st.container(key=f"home-entry-{kind}"):
                            st.html('<div class="df-home-entry-art" aria-hidden="true">'
                                    '<img src="' + _SOURCE_ART[kind] + '" alt="">'
                                    '<span>↗</span></div>')
                            st.button(mode, key=f"overview:{mode}", on_click=start_mode,
                                      args=(mode, preset), width="stretch")
                            st.caption(detail)
                with st.container(key="home-manual-entry"):
                    description, action = st.columns([1.6, 1], gap="small", vertical_alignment="center")
                    with description:
                        st.html('<div class="df-home-manual"><b>✎</b><span><strong>图片 + 文字</strong>'
                                '<small>添加图片，编写问题与参考答案。</small></span></div>')
                    action.button("人工制作图文", key="overview-manual", on_click=start_manual, width="stretch")
                with st.container(key="home-strategy-options"):
                    st.caption("按数据类型开始 · 工作台内可组合多类目标")
                    targets = ("cpt", "sft", "dpo", "orpo", "rlaif", "agent", "multiturn", "cot", "gsm8k")
                    for offset in range(0, len(targets), 3):
                        for column, target in zip(st.columns(3, gap="small"), targets[offset:offset + 3]):
                            column.button(TARGET_LABELS[target], key=f"overview-target:{target}",
                                          on_click=start_target, args=(target,), width="stretch")
        with recent_column:
            with st.container(border=True, key="home-recent-panel"):
                section_heading("最近任务", "选择任务直接查看工作流过程", "◷")
                if recent:
                    status_labels = {"completed": "已完成", "needs_attention": "需检查", "running": "处理中",
                                     "queued": "排队中", "failed": "失败", "cancelled": "已停止"}
                    for row in recent:
                        status = str(row.get("status", "未知"))
                        targets = "、".join(str(target).upper() for target in row.get("targets", [])) or "—"
                        updated = str(row.get("updated_at") or row.get("created_at") or "")[:16].replace('T', ' ') or "—"
                        task, action = st.columns([4, 1], gap="small", vertical_alignment="center")
                        with task:
                            st.html('<div class="df-home-task"><strong data-user-content>'
                                    + html.escape(str(row.get("name", "未命名任务")))
                                    + '</strong><span class="df-home-task-status" data-status="'
                                    + html.escape(status, quote=True) + '">'
                                    + html.escape(status_labels.get(status, status)) + '</span><small>'
                                    + html.escape(targets) + ' · ' + html.escape(updated) + '</small></div>')
                        with action:
                            if row.get("id"):
                                st.button("查看 →", key=f"overview-run:{row['id']}", on_click=open_run,
                                          args=(row['id'],), width="stretch")
                else:
                    st.html('<div class="df-home-empty df-home-empty-work"><span aria-hidden="true">◈</span><strong>暂无最近任务</strong>'
                            '<small>本机还没有工作流任务。上传资料或选择训练策略，即可开始创建。</small></div>')
                st.button("查看全部任务 →", on_click=navigate, args=("任务管理",),
                          key="overview-all-tasks", width="stretch")

    with source_column:
        with st.container(border=True, key="home-library-panel"):
            section_heading("来源文件", "本机缓存中的部分输入资料", "▤")
            if inventory:
                rows = []
                for item in inventory[:5]:
                    relative = item['name']
                    ext = item['extension'] or 'FILE'
                    rows.append('<div class="df-home-source"><b>' + html.escape(ext[:4])
                                + '</b><strong data-user-content title="' + html.escape(relative, quote=True)
                                + '">' + html.escape(relative) + '</strong><small>'
                                + f'{item["size"] / 1024:.1f} KB' + '</small></div>')
                st.html('<div class="df-home-source-list">' + ''.join(rows) + '</div>')
            else:
                st.html('<div class="df-home-empty"><strong>尚无来源文件</strong>'
                        '<small>可以在数据生成页上传文档，也可以用开放需求直接开始。</small></div>')
            st.button("打开数据管理", on_click=navigate, args=("数据管理",),
                      key="overview-assets", width="stretch")
    with recent_column:
        with st.container(border=True, key="home-results-panel"):
            section_heading("审核与输出", "逐条查看来源与模型判断，确认后再发布训练版本。", "✓")
            st.button("进入人工审核", on_click=navigate, args=("人工审核",),
                      key="overview-review", width="stretch")
            try:
                release_count = sum(bool(row.get("verified")) for row in application.list_releases())
            except (OSError, ValueError):
                release_count = 0
            st.html(f'<div class="df-home-summary">本地发布版本 <b>{release_count}</b></div>')
            st.button("查看输出打包", on_click=navigate, args=("输出打包",), width="stretch")
            with st.expander("本机缓存位置"):
                language = st.session_state.get('ui_language', 'zh')
                st.caption(UntranslatedText(f"{translate('来源目录', language)}　{source_location}"))
                st.caption(UntranslatedText(f"{translate('任务及审核产物目录', language)}　{output_location}"))
    job_status()

