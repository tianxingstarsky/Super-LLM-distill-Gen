"""Workspace overview. Storage and navigation are supplied by composition."""
import html
import streamlit as st
from lib.application.workflow_service import WorkflowApplication
from lib.presentation.streamlit.home_style import HOME_STYLE
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.i18n import UntranslatedText, translate


def render_overview(application: WorkflowApplication, ws, inventory, source_total, sample_count,
                    source_location, output_location, *, navigate, open_folder, job_status):
    page_header("人工智能数据生成与管理平台", "从文档、智能体上下文和开放需求，构建可追溯的高质量训练数据。", "BETTER DATA　·　A BRIGHTER AI")
    st.html(HOME_STYLE)

    def start_mode(mode: str, preset: str | None = None) -> None:
        st.session_state['workflow-source-mode'] = mode
        if preset:
            st.session_state[f'workflow-preset:{ws}'] = preset
        st.session_state['nav'] = '自动工作流'

    def start_strategy(preset: str) -> None:
        st.session_state[f'workflow-preset:{ws}'] = preset
        st.session_state['nav'] = '自动工作流'

    def open_run(run_id: str) -> None:
        st.session_state[f'task-view:{ws}'] = '数据工作流'
        st.session_state[f'task-center-run:{ws}'] = run_id
        st.session_state[f'workflow-selected:{ws}'] = run_id
        st.session_state['nav'] = '任务管理'

    runs = []
    try:
        runs = application.task_runs()
    except OSError:
        pass
    recent = sorted(runs, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
                    reverse=True)[:5]
    total = len(runs)
    completed = sum(row.get("status") == "completed" for row in runs)
    processing = sum(row.get("status") in {"running", "queued"} for row in runs)
    attention = sum(row.get("status") in {"failed", "needs_attention"} for row in runs)
    source_count = "500+" if source_total > 500 else str(source_total)
    try:
        # A manifest alone can describe an unfinished or damaged version.
        release_count = sum(bool(row.get("verified")) for row in
                            application.list_releases())
    except (OSError, ValueError):
        release_count = 0
    with st.container(key="home-top"):
        source_column, strategy_column, stats_column = st.columns([1.18, 1.02, .82], gap="small")
        with source_column:
            with st.container(border=True, key="home-source-panel"):
                section_heading("生成类型", "选择输入来源，进入自动工作流。", "◈")
                with st.container(key="home-source-entries"):
                    cards = st.columns(3, gap="small")
                    entries = (
                        ("document", "PDF", "文档资料", "上传资料并保留原文位置。", "PDF · DOCX · TXT", "文档资料", "自动推荐"),
                        ("agent", "••", "Agent 上下文", "导入对话、工具调用和观测。", "JSON · JSONL", "Agent 上下文", "Agent 轨迹"),
                        ("brief", "✦", "开放需求", "描述场景，生成多样化候选。", "领域 · 场景 · 任务", "开放需求", "自动推荐"),
                    )
                    for column, (kind, icon, title, detail, formats, mode, preset) in zip(cards, entries):
                        with column:
                            st.html(f'<div class="df-home-entry" data-kind="{kind}"><span class="df-home-entry-icon" aria-hidden="true"><i></i><b>{icon}</b></span>'
                                    f'<strong>{title}</strong><p>{detail}</p><em>{formats}</em></div>')
                            st.button("开始配置 →", key=f"overview:{mode}", on_click=start_mode,
                                      args=(mode, preset), width="stretch")
                st.button("进入数据生成工作台 →", type="primary", on_click=navigate,
                          args=("自动工作流",), key="overview-open-workflow", width="stretch")
        with strategy_column:
            with st.container(border=True, key="home-strategy-panel"):
                section_heading("训练策略选择", "按目标预填配方，进入后仍可调整。", "▤")
                with st.container(key="home-strategy-options"):
                    for kind, label, name, detail, preset in zip(
                            ("cpt", "sft", "dpo"), ("CPT", "SFT", "ORPO"),
                            ("持续预训练", "监督微调", "偏好优化"),
                            ("清洗与分块语料", "指令对话与多轮", "DPO · RLAIF"),
                            ("预训练语料", "多轮对话", "ORPO 数据生成")):
                        info, action = st.columns([1.7, 1], gap="small", vertical_alignment="center")
                        with info:
                            st.html(f'<div class="df-home-strategy-card" data-kind="{kind}"><b>{label}</b>'
                                    f'<strong>{name}</strong><small>{detail}</small></div>')
                        with action:
                            st.button(f"选用 {label} →", key=f"overview-preset:{preset}",
                                      on_click=start_strategy, args=(preset,), width="stretch")
                st.button("使用自动推荐方案", on_click=start_strategy, args=("自动推荐",),
                          key="overview-preset:auto", width="stretch")
        with stats_column:
            with st.container(border=True, key="home-stats-panel"):
                section_heading("任务统计", "当前工作区的真实运行状态", "◷")
                items = (("工作流总数", total, "blue"), ("已完成", completed, "green"),
                         ("处理中", processing, "amber"), ("需检查", attention, "red"))
                st.html('<div class="df-home-kpis">' + ''.join(
                    f'<div class="df-home-kpi" data-tone="{tone}"><small>{label}</small><strong>{value}</strong></div>'
                    for label, value, tone in items) + '</div>')
                st.html(f'<div class="df-home-summary"><span>源文件 <b>{source_count}</b></span>'
                        f'<span>样本文件 <b>{sample_count}</b></span>'
                        f'<span>本地发布版本 <b>{release_count}</b></span></div>')
                st.button("查看全部任务 →", on_click=navigate, args=("任务管理",),
                          key="overview-all-tasks", width="stretch")
    left, right = st.columns([1.78, 1], gap="small")
    with left:
        with st.container(border=True):
            section_heading("工作流", "从输入到训练包，每一步都可查看状态、证据与产物。", "⌁")
            st.html('<div class="df-home-flow">'
                    '<div class="df-home-flow-step"><b>01</b><strong>输入数据</strong><small>文档 / 对话 / 需求</small></div>'
                    '<div class="df-home-flow-step"><b>02</b><strong>数据解析</strong><small>抽取 / 分块</small></div>'
                    '<div class="df-home-flow-step"><b>03</b><strong>数据处理</strong><small>生成 / 清洗</small></div>'
                    '<div class="df-home-flow-step"><b>04</b><strong>质量审核</strong><small>自动 / 人工</small></div>'
                    '<div class="df-home-flow-step"><b>05</b><strong>输出数据</strong><small>校验 / 打包</small></div></div>')
            st.button("查看可视化工作流 →", on_click=navigate, args=("自动工作流",),
                      key="overview-flow", width="stretch")
        with st.container(border=True):
            section_heading("人工审核 / 模型对齐", "逐条查看来源与模型判断，确认后再发布训练版本。", "✓")
            st.html('<div class="df-home-review-grid">'
                    '<div class="df-home-review-card" data-kind="sft"><b>SFT</b><strong>指令数据调整</strong><small>检查对话上下文、修订回答并留痕。</small></div>'
                    '<div class="df-home-review-card" data-kind="dpo"><b>ORPO · DPO · RLAIF</b><strong>偏好对优化</strong><small>比较候选回答，确认两类偏好方向。</small></div>'
                    '<div class="df-home-review-card" data-kind="cpt"><b>CPT</b><strong>语料审阅</strong><small>核对原文位置、质量信号与保留范围。</small></div>'
                    '</div>')
            st.button("进入人工审核", on_click=navigate, args=("人工审核",),
                      key="overview-review", width="stretch")
        with st.container(border=True):
            section_heading("来源文件", "当前工作区中的部分输入资料", "▤")
            if inventory:
                rows = []
                for item in inventory[:6]:
                    relative = item['name']
                    ext = item['extension'] or 'FILE'
                    rows.append('<div class="df-home-source"><b>' + html.escape(ext[:4]) + '</b><strong data-user-content title="'
                                + html.escape(relative, quote=True) + '">' + html.escape(relative)
                                + '</strong><small>' + f'{item["size"] / 1024:.1f} KB' + '</small></div>')
                st.html('<div class="df-home-source-list">' + ''.join(rows) + '</div>')
                if source_total > 6:
                    st.caption(f"还可在数据管理中查看其余 {min(source_total, 500) - 6}{'+' if source_total > 500 else ''} 个文件。")
            else:
                st.html('<div class="df-home-empty"><b>▤</b><strong>尚无来源文件</strong>'
                        '<small>可以在数据生成页上传文档，也可以用开放需求直接开始。</small></div>')
            st.button("打开数据管理", on_click=navigate, args=("数据管理",),
                      key="overview-assets", width="stretch")
            st.button("打开已有文件夹", on_click=open_folder,
                      key="overview-open-folder", width="stretch")
    with right:
        with st.container(border=True):
            section_heading("最近任务", "选择任务直接查看工作流过程", "◷")
            if recent:
                status_labels = {"completed": "已完成", "needs_attention": "需检查", "running": "处理中",
                                 "queued": "排队中", "failed": "失败", "cancelled": "已停止"}
                for row in recent:
                    status = str(row.get("status", "未知"))
                    targets = "、".join(str(target).upper() for target in row.get("targets", [])) or "—"
                    updated = str(row.get("updated_at") or "")[:16].replace('T', ' ') or "—"
                    task, action = st.columns([3.2, 1], gap="small", vertical_alignment="center")
                    with task:
                        st.html('<div class="df-home-task"><span class="df-home-task-icon" data-status="'
                                + html.escape(status, quote=True) + '">'
                                + ('✓' if status == 'completed' else '!' if status in {'failed', 'needs_attention'} else '◷')
                                + '</span><strong data-user-content>' + html.escape(str(row.get("name", "未命名任务")))
                                + '</strong><span class="df-home-task-status" data-status="'
                                + html.escape(status, quote=True) + '">' + html.escape(status_labels.get(status, status))
                                + '</span><small>' + html.escape(targets) + ' · ' + html.escape(updated) + '</small></div>')
                    with action:
                        if row.get("id"):
                            st.button("查看 →", key=f"overview-run:{row['id']}", on_click=open_run,
                                      args=(row['id'],), width="stretch")
            else:
                st.html('<div class="df-home-empty"><b>⌁</b><strong>暂无最近任务</strong>'
                        '<small>当前工作区还没有工作流任务。选择来源或训练策略，即可开始创建。</small></div>')
        with st.container(border=True):
            section_heading("输出与存储", "已生成版本和工作区位置", "⇩")
            st.html(f'<div class="df-home-summary"><span>本地发布版本 <b>{release_count}</b></span>'
                    f'<span>当前来源 <b>{source_count}</b></span></div>')
            st.button("查看输出打包", on_click=navigate, args=("输出打包",), width="stretch")
            with st.expander("工作区路径与存储位置"):
                language = st.session_state.get('ui_language', 'zh')
                st.caption(UntranslatedText(f"{translate('来源目录', language)}　{source_location}"))
                st.caption(UntranslatedText(f"{translate('任务及审核产物目录', language)}　{output_location}"))
    job_status()

