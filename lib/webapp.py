"""Chinese operations console: session-isolated workspaces and schema-driven forms."""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
_BRAND_MARK = base64.b64encode((ROOT / "assets" / "brand" / "shujian-cube-mark.png").read_bytes()).decode("ascii")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import streamlit as st
from lib.presentation.streamlit.i18n import canonical_navigation_route, initialize_language, install_streamlit_localization, set_language_from_choice, translate_label
from filelock import Timeout
from lib.render import MESSAGE_CSS, render_message_sequence
from lib.console_jobs import Job
from lib.bootstrap.workspaces import workspace_application
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.presentation.streamlit.home_style import HOME_STYLE

st.set_page_config(page_title="数简立方 · ShuJian Cube", layout="wide", initial_sidebar_state="expanded")
initialize_language(st)
install_streamlit_localization()
from lib.console_theme import CSS as CHROME_CSS
# Keep Streamlit's native sidebar controls in charge of opening and closing it.
st.html(f"<style>{MESSAGE_CSS}\n{CHROME_CSS}</style>")
WORKSPACES = workspace_application()


def _ws_out():
    return WORKSPACES.output(st.session_state.get("ws", "default"))


def _OUT(name):
    return _ws_out() / name


def _request_folder_dialog():
    st.session_state['open-folder-dialog'] = True


def _close_folder_dialog():
    st.session_state.pop('open-folder-dialog', None)


def _select_page(page: str):
    st.session_state["nav"] = page


def _set_ui_language():
    set_language_from_choice(st, st.session_state.get("ui-language-choice", "简体中文"))


@st.dialog("打开已有文件夹", width="large", on_dismiss=_close_folder_dialog)
def _open_folder_dialog():
    st.write("选择你已经准备好的目录。不会复制、搬走或重命名原文件。")
    if st.button("浏览本机文件夹…", width="stretch"):
        try:
            from lib.folder_picker import choose_existing
            selected = choose_existing()
            if selected:
                st.session_state['open-folder-path'] = selected
        except Exception:
            st.warning("当前环境没有本机目录选择器，请粘贴目录路径。")
    with st.form("open-folder"):
        path = st.text_input("已有文件夹路径", key="open-folder-path", placeholder="F:\\资料\\我的数据集")
        submitted = st.form_submit_button("打开文件夹", type="primary", width="stretch")
    if submitted:
        try:
            identifier = WORKSPACES.open_folder(path)
            st.session_state['folder-to-open'] = identifier
            _close_folder_dialog()
            st.rerun()
        except (ValueError, OSError) as error:
            st.error(str(error))


def _ws_choice():
    st.sidebar.html(f'<div class="df-brand-line"><img class="df-brand-mark" src="data:image/png;base64,{_BRAND_MARK}" alt="" /><span><span class="df-brand">数简立方</span><span class="df-kicker">数据简单生成</span></span></div>')
    options = WORKSPACES.available_workspaces()
    if 'folder-to-open' in st.session_state:
        st.session_state['ws'] = st.session_state.pop('folder-to-open')
    if 'ws' not in st.session_state:
        st.session_state['ws'] = st.query_params.get('ws') or WORKSPACES.resolve()
    if st.session_state['ws'] not in options:
        st.sidebar.warning("链接中的文件夹尚未在本机打开，请选择已有目录。")
        st.session_state['ws'] = WORKSPACES.default_id
    previous = st.session_state.get('last-workspace', st.session_state['ws'])
    return options, previous


def _sample_files():
    candidates = {p.resolve() for p in _ws_out().glob("*.jsonl") if "samples" in p.name or "combined_preview" in p.name}
    try:
        from lib.bootstrap.workflows import workflow_application
        candidates.update(Path(path).resolve() for path in workflow_application(ROOT, _ws_out()).reviewable_artifacts())
    except (OSError, ValueError):
        pass
    if st.session_state['ws'] != WORKSPACES.default_id:
        candidates.update(p.resolve() for p in WORKSPACES.source_files(st.session_state['ws'], suffixes=(".jsonl",)))
    return sorted(candidates)


@st.cache_data(show_spinner=False, max_entries=8)
def _load_normalized_samples(path_key: str, mtime_ns: int):
    """按 路径+mtime 缓存已规范化样本：预览翻页/表单重渲染不再整读 JSONL。

    大文件（上万条）在每次控件交互都会触发全量 read+normalize，是预览页卡顿的
    直接来源；LRU 上限 8 个文件控制内存，文件变更（mtime 变化）自动失效。"""
    from pathlib import Path as _Path
    from lib.bootstrap.releases import release_application
    from lib.review_editor import normalize_sample
    return [normalize_sample(row) for row in release_application().read_samples(_Path(path_key))]


@st.cache_data(show_spinner=False, max_entries=8)
def _load_raw_samples(path_key: str, mtime_ns: int):
    """Cache source rows for quality filters without hiding structural errors."""
    from pathlib import Path as _Path
    from lib.bootstrap.releases import release_application
    return release_application().read_samples(_Path(path_key))


def _selected_samples(key):
    files = _sample_files()
    if not files:
        st.info("当前工作区暂无样本")
        return None, []
    source = st.selectbox("样本文件", files, format_func=lambda p: str(p.relative_to(WORKSPACES.folder(st.session_state['ws']))) if p.is_relative_to(WORKSPACES.folder(st.session_state['ws'])) else p.name,
                          key=f"{key}:{st.session_state['ws']}")
    return source, _load_normalized_samples(str(source), source.stat().st_mtime_ns)


def _gate():
    from lib.gates import GateKeeper
    return GateKeeper(ROOT / "configs/gates.yaml", _OUT("gates_state.json"))


def _command_meta():
    from lib.extensions import load_commands
    return load_commands(ROOT)


def _begin(command):
    ws = st.session_state["ws"]
    key = "job:" + ws
    existing = st.session_state.get(key)
    if existing and existing.snapshot()[0] is None:
        st.warning("当前工作区已有运行任务")
        return
    job = Job(command, ws, ROOT)
    try:
        job.start()
    except Timeout:
        # 任务登记在浏览器会话里；换窗口/刷新后看不到旧任务，靠工作区级锁挡住并发写入
        st.warning("该工作区已有任务在运行（可能在其他窗口启动），请等待完成后再试。")
        return
    st.session_state[key] = job


@st.fragment(run_every=1)
def _job_status():
    job = st.session_state.get("job:" + st.session_state["ws"])
    if not job:
        return
    code, lines = job.snapshot()
    if code is None:
        st.info("任务运行中")
    elif code == 0:
        st.success("任务完成")
    else:
        st.error(f"任务未完成，退出码 {code}")
    st.code("\n".join(lines[-80:]) or "等待输出", language="text")


def page_overview():
    page_header("人工智能数据生成与管理平台", "从文档、智能体上下文和开放需求，构建可追溯的高质量训练数据。", "BETTER DATA　·　A BRIGHTER AI")
    source = WORKSPACES.folder(st.session_state['ws'])
    ws = st.session_state['ws']
    inventory = WORKSPACES.source_files(ws, limit=501)
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
        from lib.bootstrap.workflows import workflow_application
        runs = workflow_application(ROOT, _ws_out()).list_runs()
    except OSError:
        pass
    recent = sorted(runs, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
                    reverse=True)[:5]
    total = len(runs)
    completed = sum(row.get("status") == "completed" for row in runs)
    processing = sum(row.get("status") in {"running", "queued"} for row in runs)
    attention = sum(row.get("status") in {"failed", "needs_attention"} for row in runs)
    source_count = "500+" if len(inventory) > 500 else str(len(inventory))
    try:
        # A manifest alone can describe an unfinished or damaged version.
        release_count = sum(bool(row.get("verified")) for row in
                            workflow_application(ROOT, _ws_out()).list_releases())
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
                st.button("进入数据生成工作台 →", type="primary", on_click=_select_page,
                          args=("自动工作流",), key="overview-open-workflow", width="stretch")
        with strategy_column:
            with st.container(border=True, key="home-strategy-panel"):
                section_heading("训练策略选择", "按目标预填配方，进入后仍可调整。", "▤")
                with st.container(key="home-strategy-options"):
                    for kind, label, name, detail, preset in zip(
                            ("cpt", "sft", "dpo"), ("CPT", "SFT", "DPO"),
                            ("持续预训练", "监督微调", "偏好优化"),
                            ("清洗与分块语料", "指令对话与多轮", "ORPO · RLAIF"),
                            ("预训练语料", "多轮对话", "偏好对齐")):
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
                        f'<span>样本文件 <b>{len(_sample_files())}</b></span>'
                        f'<span>本地发布版本 <b>{release_count}</b></span></div>')
                st.button("查看全部任务 →", on_click=_select_page, args=("任务管理",),
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
            st.button("查看可视化工作流 →", on_click=_select_page, args=("自动工作流",),
                      key="overview-flow", width="stretch")
        with st.container(border=True):
            section_heading("人工审核 / 模型对齐", "逐条查看来源与模型判断，确认后再发布训练版本。", "✓")
            st.html('<div class="df-home-review-grid">'
                    '<div class="df-home-review-card" data-kind="sft"><b>SFT</b><strong>指令数据调整</strong><small>检查对话上下文、修订回答并留痕。</small></div>'
                    '<div class="df-home-review-card" data-kind="dpo"><b>DPO · ORPO</b><strong>偏好对优化</strong><small>比较候选回答，确认两类偏好方向。</small></div>'
                    '<div class="df-home-review-card" data-kind="cpt"><b>CPT</b><strong>语料审阅</strong><small>核对原文位置、质量信号与保留范围。</small></div>'
                    '</div>')
            st.button("进入人工审核", on_click=_select_page, args=("人工审核",),
                      key="overview-review", width="stretch")
        with st.container(border=True):
            section_heading("来源文件", "当前工作区中的部分输入资料", "▤")
            if inventory:
                rows = []
                for path in inventory[:6]:
                    relative = str(path.relative_to(source))
                    ext = path.suffix.upper().lstrip('.') or 'FILE'
                    rows.append('<div class="df-home-source"><b>' + html.escape(ext[:4]) + '</b><strong title="'
                                + html.escape(relative, quote=True) + '">' + html.escape(relative)
                                + '</strong><small>' + f'{path.stat().st_size / 1024:.1f} KB' + '</small></div>')
                st.html('<div class="df-home-source-list">' + ''.join(rows) + '</div>')
                if len(inventory) > 6:
                    st.caption(f"还可在数据管理中查看其余 {min(len(inventory), 500) - 6}{'+' if len(inventory) > 500 else ''} 个文件。")
            else:
                st.html('<div class="df-home-empty"><b>▤</b><strong>尚无来源文件</strong>'
                        '<small>可以在数据生成页上传文档，也可以用开放需求直接开始。</small></div>')
            st.button("打开数据管理", on_click=_select_page, args=("数据管理",),
                      key="overview-assets", width="stretch")
            st.button("打开已有文件夹", on_click=_request_folder_dialog,
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
                                + '</span><strong>' + html.escape(str(row.get("name", "未命名任务")))
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
            st.button("查看输出打包", on_click=_select_page, args=("输出打包",), width="stretch")
            with st.expander("工作区路径与存储位置"):
                st.caption(f"来源目录　{source.as_posix()}")
                st.caption(f"任务及审核产物目录　{_ws_out().as_posix()}")
    _job_status()


def page_preview(show_title=True):
    if show_title:
        page_header("数据预览", "浏览已校验的语料、对话、偏好对与工具轨迹。", "SAMPLE PREVIEW")
    from lib.presentation.streamlit.data_management_style import DATA_MANAGEMENT_STYLE
    st.html(DATA_MANAGEMENT_STYLE)
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.dataset_preview_page import render_workflow_samples
    workflow_app = workflow_application(ROOT, _ws_out())
    verified_runs = [row for row in workflow_app.list_runs()
                     if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if verified_runs:
        view = st.segmented_control(
            "预览来源", ("工作流产物", "已有对话文件"), default="工作流产物",
            key=f"preview-source:{st.session_state['ws']}", label_visibility="collapsed",
        )
        if view == "工作流产物":
            render_workflow_samples(workflow_app, st.session_state["ws"])
            return
    left, right = st.columns([1, 2.15], gap="large")
    with left, st.container(border=True):
        section_heading("选择样本", "从当前工作区已有的对话文件浏览", "▤")
        try:
            source, samples = _selected_samples("preview-file")
        except (OSError, ValueError) as error:
            st.error(f"无法预览当前文件：{error}")
            return
        if samples:
            index = st.number_input("样本序号", 1, len(samples), 1)
            st.caption(f"当前文件共 {len(samples):,} 条 · 正在查看第 {index:,} 条")
    if not samples:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">◉</span><strong>还没有可预览的样本</strong><p>先选择已有数据文件，或从“数据生成”创建一条包含对话、推理或工具调用轨迹的工作流。</p></div>')
        return
    sample = samples[index - 1]
    messages = [message for message in sample.get("messages", []) if isinstance(message, dict)]
    from lib.presentation.streamlit.artifact_preview import (
        _tool_call_names, _tool_result_user, render_training_sample,
    )
    tool_calls = sum(len(_tool_call_names(message)) for message in messages)
    reasoning = sum(bool(message.get("reasoning_content") or message.get("reasoning"))
                    for message in messages)
    def has_tool_error(message: dict) -> bool:
        content = message.get("content")
        return isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")
            for block in content
        )

    errors = sum(bool(message.get("isError") or message.get("is_error"))
                 or has_tool_error(message)
                 for message in messages)
    user_turns = sum(message.get("role") == "user" and not _tool_result_user(message)
                     for message in messages)
    content_type = "Agent 工具轨迹" if tool_calls or any(message.get("role") == "tool" or _tool_result_user(message)
                                                   for message in messages) else "多轮对话" if user_turns > 1 else "问答样本"
    target = "agent" if content_type == "Agent 工具轨迹" else "multiturn" if user_turns > 1 else "sft"
    with left, st.container(border=True):
        section_heading("样本概览", "这些字段来自当前选中的记录", "◉")
        st.html(
            '<div class="df-data-sample-facts">'
            f'<div><span>样本 ID</span><b>{html.escape(str(sample.get("id", index)))}</b></div>'
            f'<div><span>内容类型</span><b>{html.escape(content_type)}</b></div>'
            f'<div><span>消息 / 用户轮次</span><b>{len(messages)} / {user_turns}</b></div>'
            f'<div><span>工具调用</span><b>{tool_calls}</b></div>'
            f'<div><span>含推理记录</span><b>{reasoning}</b></div>'
            f'<div><span>工具错误</span><b>{errors}</b></div>'
            '</div>'
            '<p class="df-data-note">文件浏览仅展示原有样本内容；是否可用于训练请查看质量报告与人工审核。</p>'
        )
        st.caption("样本 ID: " + str(sample.get("id", index)))
    with right, st.container(border=True):
        section_heading("样本内容", "按对话轮次展开上下文与工具调用", "◉")
        st.html('<div class="df-data-sample-head"><strong>' + html.escape(source.name if source else "样本")
                + '</strong><span>第 ' + str(index) + ' / ' + str(len(samples)) + ' 条</span></div>')
        st.html(render_training_sample(target, sample))


def page_workflow():
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.workflow_page import render_workbench
    render_workbench(workflow_application(ROOT, _ws_out()), _begin)


def page_run(show_title=True):
    if show_title:
        page_header("高级命令工具", "按任务选择工具，只填写必要参数；运行记录会保留命令与输出。", "ADVANCED TOOLS")
    presets = {
        "minimind 兼容草稿导出": ("export", {"format": "minimind", "bulk": False}),
        "文档语料整理": ("doc2corpus", {}),
        "文档问答生成": ("doc2data", {}),
        "质量报告": ("quality-report", {}),
        "AI 审核小队": ("dsh", {"team": True, "task": "先读取 review-team 技能，依据用户选定的工作区与审核配置派发子智能体。未提供配置先报告缺项，不创建账号、不自行放行。"}),
        "协作者拉取": ("review-remote", {"action": "pull"}),
        "环境自检": ("doctor", {}),
        "全部命令": (None, {}),
    }
    descriptions = {
        "minimind 兼容草稿导出": "将已有审核结果转换为 MiniMind 可读取的草稿文件。",
        "文档语料整理": "解析文档并清洗、分块，形成可追溯语料。",
        "文档问答生成": "从文档生成问答候选，并保留来源片段。",
        "质量报告": "统计已有样本的质量问题与检查结果。",
        "AI 审核小队": "使用工作区审核配置处理需要专家复核的任务。",
        "协作者拉取": "拉取已配置协作服务中的待审核记录。",
        "环境自检": "检查本机工作区与模型服务的运行条件。",
        "全部命令": "使用完整命令目录；适合熟悉命令行参数的操作者。",
    }
    with st.container(border=True):
        section_heading("选择工具", "自动数据生成请使用“数据生成”；这里保留原有命令工具。", "⚙")
        preset = st.selectbox("任务", list(presets), key=f"command-preset:{st.session_state['ws']}")
        st.caption(descriptions[preset])
    command, defaults = presets[preset]
    from lib.cli import build_parser
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    if command is None:
        available = [c for c in _command_meta() if c not in ("review-server", "user")]
        command = st.selectbox("选择完整命令", available)
    action_parser = sub[command]
    keybase = f"form:{st.session_state['ws']}:{preset}:{command}"
    values = []
    actions = [a for a in action_parser._actions if a.dest not in ("help", "ws")]
    primary_dests = {
        "export": {"format", "input", "out"},
        "doc2corpus": {"input", "out", "chunk_size"},
        "doc2data": {"input", "mode", "qa_per_chunk"},
        "quality-report": {"input"},
        "dsh": {"task", "review_config"},
        "review-remote": {"action", "config", "batch"},
    }.get(command, {"input", "out", "task"})
    primary = [action for action in actions if action.required or action.dest in primary_dests]
    advanced = [action for action in actions if action not in primary]

    def render_field(action):
        default = defaults.get(action.dest, action.default)
        label = action.dest.replace("_", " ")
        labels = {"input": "输入路径", "out": "输出目录", "format": "训练格式", "bulk": "放量导出（需审核）", "tag": "版本标签", "model": "模型", "backend": "模型后端", "action": "操作", "config": "审核配置路径", "batch": "每批条数", "task": "任务要求", "team": "启用子智能体团队"}
        label = labels.get(action.dest, label)
        if action.required:
            label += "（必填）"
        key = keybase + ":" + action.dest
        if action.dest == "review_config":
            candidates = sorted(str(p) for p in (ROOT / "configs").glob("review_remote*.yaml") if "example" not in p.name)
            selected = st.multiselect("审核配置", candidates, format_func=lambda p: Path(p).name, key=key)
            value = []
            for filename in selected:
                import yaml
                config = yaml.safe_load(Path(filename).read_text(encoding="utf-8")) or {}
                value.append(config.get("policy", "safety" if "safety" in filename else "quality") + "=" + filename)
        elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            value = st.checkbox(label, value=bool(default), key=key)
        elif action.choices:
            choices = list(action.choices)
            if command == "review-remote" and action.dest == "action":
                choices = [c for c in choices if c not in ("setup", "human")]
            if command == "review" and action.dest == "action":
                choices = [c for c in choices if c != "app"]
            value = st.selectbox(label, choices, index=choices.index(default) if default in choices else 0, key=key)
        elif action.type is int and default is not None:
            value = st.number_input(label, min_value=0, value=int(default), step=1, key=key)
        else:
            value = st.text_input(label, value=str(default or ""), key=key)
        values.append((action, value))

    with st.container(border=True):
        section_heading("任务参数", descriptions[preset], "▤")
        with st.form(keybase):
            if primary:
                columns = st.columns(2)
                for index, action in enumerate(primary):
                    with columns[index % 2]:
                        render_field(action)
            else:
                st.info("这项检查不需要额外参数，可以直接运行。")
            if advanced:
                with st.expander(f"更多参数（{len(advanced)} 项）"):
                    columns = st.columns(2)
                    for index, action in enumerate(advanced):
                        with columns[index % 2]:
                            render_field(action)
            submit = st.form_submit_button("运行", type="primary")
    if submit:
        missing = [action.dest for action, value in values if action.required and value in (None, "")]
        if missing:
            # 必填参数留空直接提交只会得到晦涩的"退出码 2"；在提交前明确指出缺什么
            st.warning("请填写必填参数：" + "、".join(d.replace("_", " ") for d in missing))
        else:
            argv = [command]
            for action, value in values:
                if isinstance(action, argparse._AppendAction):
                    for entry in value:
                        argv.extend([action.option_strings[0], str(entry)])
                elif isinstance(action, argparse._StoreTrueAction):
                    if value:
                        argv.append(action.option_strings[0])
                elif value not in (None, ""):
                    if action.option_strings:
                        argv.append(action.option_strings[0])
                    argv.append(str(value))
            _begin(argv)
    _job_status()


def page_review():
    from lib.review_management import render_management, reviewer_identity
    from lib.review_workspace import render_workspace

    dataset = WORKSPACES.dataset_name(st.session_state['ws'])
    mode = st.session_state.get('review-management')
    if mode:
        render_management(mode, dataset, _selected_samples)
        return

    def navigate(target):
        st.session_state['review-management'] = target

    try:
        username = reviewer_identity()
        render_workspace(dataset, username, st.session_state['ws'],
                         gate=_gate(), on_navigate=navigate)
    except PermissionError:
        st.error('当前身份没有此数据集的权限，请切换身份或由管理员授予权限。')
        if st.button('审核身份与接入'):
            navigate('settings')
            st.rerun()


def page_preference_review():
    from lib.bootstrap.preference_reviews import preference_review_application
    from lib.presentation.streamlit.preference_review_page import render_preference_review
    render_preference_review(preference_review_application(_ws_out()))


def page_corpus_review():
    from lib.bootstrap.corpus_reviews import corpus_review_application
    from lib.presentation.streamlit.corpus_review_page import render_corpus_review
    render_corpus_review(corpus_review_application(_ws_out()))


def page_human_review():
    page_header("人工审核 / 模型对齐", "在发布前检查样本质量、修订内容并保留审核历史。", "CPT　·　SFT　·　DPO　·　ORPO")
    modes = ("SFT 数据调整", "DPO 偏好优化", "ORPO 偏好优化", "CPT 语料审核")
    mode_key = f"review-mode:{st.session_state['ws']}"
    from lib.presentation.streamlit.review_overview import render_review_overview
    applications = render_review_overview(_ws_out(), mode_key)
    mode = st.segmented_control("审核类型", modes, default=modes[0], key=mode_key,
                                label_visibility="collapsed")
    if mode == modes[0]:
        from lib.presentation.streamlit.sft_review_page import render_sft_review
        render_sft_review(applications[mode], legacy_review=page_review)
    elif mode == modes[1]:
        from lib.presentation.streamlit.preference_review_page import render_preference_review
        render_preference_review(applications[mode], show_header=False)
    elif mode == modes[2]:
        from lib.presentation.streamlit.preference_review_page import render_preference_review
        render_preference_review(applications[mode], show_header=False)
    else:
        from lib.presentation.streamlit.corpus_review_page import render_corpus_review
        render_corpus_review(applications[mode], show_header=False)


def page_output_packages():
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.package_page import render_package_page
    render_package_page(workflow_application(ROOT, _ws_out()))


def page_quality(show_title=True):
    if show_title:
        page_header("质量报告", "查看格式、内容与审核覆盖情况，定位需要修复或隔离的样本。", "DATA QUALITY")
    from lib.bootstrap.workflows import workflow_application
    from lib.presentation.streamlit.workflow_quality_page import render_workflow_quality

    workflow_app = workflow_application(ROOT, _ws_out())
    verified_runs = [row for row in workflow_app.list_runs()
                     if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if verified_runs:
        view = st.segmented_control(
            "质量范围", ("工作流质量", "已有对话质量"), default="工作流质量",
            key=f"quality-source:{st.session_state['ws']}", label_visibility="collapsed",
        )
        if view == "工作流质量":
            render_workflow_quality(workflow_app, st.session_state["ws"])
            return
    from lib.bootstrap.releases import release_application
    # A quality report must inspect the source rows, including malformed conversation
    # structures that the interactive preview intentionally refuses to normalize.
    files = _sample_files()
    if not files:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">✓</span><strong>暂无质量报告</strong><p>选取一个样本文件后，系统会统计结构问题、审核覆盖与批量放行条件。</p></div>')
        return
    ws = st.session_state["ws"]
    source_folder = WORKSPACES.folder(ws)
    source_path = st.selectbox(
        "样本文件", files,
        format_func=lambda path: str(path.relative_to(source_folder)) if path.is_relative_to(source_folder) else path.name,
        key=f"quality-file:{ws}",
    )
    try:
        samples = _load_raw_samples(str(source_path), source_path.stat().st_mtime_ns)
    except (OSError, ValueError) as error:
        st.error(f"无法读取样本文件：{error}")
        return
    if not samples:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">✓</span><strong>暂无质量报告</strong><p>选取一个样本文件后，系统会统计结构问题、审核覆盖与批量放行条件。</p></div>')
        return
    data = release_application().quality_report_for_dataset(
        samples, WORKSPACES.dataset_name(st.session_state["ws"]))
    st.html('''<style>
    .df-quality-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:10px 0 16px; }
    .df-quality-summary > div { display:grid; align-content:center; gap:5px; min-height:93px; padding:15px; border:1px solid #DFE8F4; border-radius:11px; background:linear-gradient(145deg,#fff,#F8FBFF); }
    .df-quality-summary span { color:#7B8BA0; font-size:12px; }
    .df-quality-summary strong { color:#1A2B44; font-size:25px; line-height:1.15; }
    .df-quality-summary small { color:#8A99AA; font-size:11px; }
    .df-quality-summary > div:nth-child(2) strong { color:#D2782F; }
    .df-quality-summary > div:nth-child(3) strong,.df-quality-summary > div:nth-child(4) strong { color:#1769D2; }
    .df-quality-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; margin:12px 0; }
    .df-quality-facts > div { display:grid; gap:3px; padding:10px 11px; border:1px solid #E5ECF6; border-radius:9px; background:#FBFDFF; }
    .df-quality-facts span { color:#8191A4; font-size:12px; }
    .df-quality-facts strong { color:#233951; font-size:14px; }
    .df-quality-code-list { display:flex; flex-wrap:wrap; gap:7px; margin:8px 0 14px; }
    .df-quality-code-list span { padding:5px 8px; border:1px solid #DDE8F6; border-radius:6px; background:#F5F9FF; color:#3E658F; font-size:12px; }
    .df-quality-record { display:flex; flex-wrap:wrap; gap:7px; padding:11px 0; }
    .df-quality-record span { padding:5px 8px; border:1px solid #E1EAF5; border-radius:6px; background:#F8FBFF; color:#546E8D; font-size:12px; }
    .df-quality-preview { max-height:460px; overflow:auto; padding:14px; border:1px solid #E2EAF4; border-radius:10px; background:#FCFDFF; }
    @media(max-width:900px) { .df-quality-summary { grid-template-columns:repeat(2,minmax(0,1fr)); } .df-quality-facts { grid-template-columns:1fr; } }
    </style>''')
    st.html(
        '<div class="df-quality-summary">'
        f'<div><span>样本总数</span><strong>{data["samples"]:,}</strong><small>当前选择的文件</small></div>'
        f'<div><span>结构问题</span><strong>{data["issue_count"]:,}</strong><small>逐条检查所记录的问题</small></div>'
        f'<div><span>重复内容</span><strong>{data["duplicate_content"]:,}</strong><small>依据消息等内容指纹</small></div>'
        f'<div><span>重复 ID</span><strong>{data["duplicate_ids"]:,}</strong><small>同名样本额外出现次数</small></div>'
        '</div>'
    )
    st.caption("文件：" + source_path.name + " · 本报告只说明结构与有效审核证据，不代表事实正确性。")
    left, right = st.columns([1.4, 1], gap="large")
    with left, st.container(border=True):
        section_heading("审核覆盖与放量条件", "审核记录仅在绑定当前样本内容时计入覆盖率。", "◉")
        coverage = float(data["review_coverage"])
        st.progress(coverage, text=f"有效审核覆盖 {coverage:.0%}")
        if data["ready_for_bulk"]:
            st.success("达到当前自动放量检查条件；正式放量仍需人工确认 G3。")
        else:
            block_names = {
                "empty_dataset": "空数据集", "structural_errors": "存在结构问题",
                "duplicate_samples": "存在重复样本", "review_coverage_below_90_percent": "有效审核覆盖不足 90%",
                "review_consensus_not_met": "审核共识未达成",
            }
            st.warning("当前未达到放量条件：" + "、".join(
                block_names.get(reason, reason) for reason in data["block_reasons"]))
    with right, st.container(border=True):
        section_heading("数据分布", "语言为字符启发式分类；长度按字符统计。", "▤")
        languages = data["language_heuristic"]
        lengths = data["length_chars"]
        st.html(
            '<div class="df-quality-facts">'
            f'<div><span>中文</span><strong>{int(languages.get("zh", 0)):,} 条</strong></div>'
            f'<div><span>中英混合</span><strong>{int(languages.get("mixed", 0)):,} 条</strong></div>'
            f'<div><span>英文或其他</span><strong>{int(languages.get("en_or_other", 0)):,} 条</strong></div>'
            '</div>'
        )
        st.caption(f"正文长度：最短 {lengths['min']} 字符 · 平均 {lengths['mean']} 字符 · 最长 {lengths['max']} 字符")
    with st.container(border=True):
        section_heading("问题定位", "按问题类型和样本 ID 筛选，查看原始样本及所在行。", "⌕")
        issue_names = {
            "visual_review_required": "图像需人工查看", "messages_missing": "缺少对话消息",
            "message_schema": "消息结构不符", "orphan_context": "上下文起点缺失",
            "incomplete_answer": "回答未完成", "unresolved_tool_error": "未解决的工具错误",
        }
        code_counts = {}
        for issue in data["issues"]:
            code = str(issue.get("code", "未知问题"))
            code_counts[code] = code_counts.get(code, 0) + 1
        if code_counts:
            st.html('<div class="df-quality-code-list">' + ''.join(
                '<span>' + html.escape(issue_names.get(code, code)) + ' · ' + str(count) + '</span>'
                for code, count in sorted(code_counts.items(), key=lambda item: (-item[1], item[0]))) + '</div>')
        else:
            st.success("当前文件没有逐条结构问题。重复统计和审核覆盖仍需单独核对。")
        filter_col, search_col = st.columns([1, 2])
        codes = ["全部问题", *sorted(code_counts)]
        selected_code = filter_col.selectbox("问题类型", codes,
                                             format_func=lambda code: issue_names.get(code, code))
        query = search_col.text_input("搜索样本 ID", placeholder="输入样本 ID 的任意部分…")
        filtered = [issue for issue in data["issues"]
                    if (selected_code == "全部问题" or issue.get("code") == selected_code)
                    and query.casefold() in str(issue.get("sample", "")).casefold()]
        st.caption(f"匹配 {len(filtered)} / {data['issue_count']} 条问题；重复内容与重复 ID 为单独的汇总计数。")
        if filtered:
            selected = st.selectbox(
                "定位问题样本", list(range(len(filtered))),
                format_func=lambda index: (f"{issue_names.get(filtered[index].get('code'), filtered[index].get('code'))}"
                                           f" · {filtered[index].get('sample', '未知样本')}"),
            )
            issue = filtered[selected]
            sample_id = str(issue.get("sample", ""))
            positions = [index for index, row in enumerate(samples)
                         if str(row.get("id") or f"row-{index + 1}") == sample_id]
            if positions:
                position = (st.selectbox("同名样本位置", positions, format_func=lambda index: f"文件第 {index + 1} 条")
                            if len(positions) > 1 else positions[0])
                sample = samples[position]
                st.html('<div class="df-quality-record"><span>文件第 ' + str(position + 1)
                        + ' 条</span><span>样本 ID：' + html.escape(sample_id)
                        + '</span><span>问题：' + html.escape(issue_names.get(str(issue.get('code')), str(issue.get('code'))))
                        + '</span></div>')
                messages = sample.get("messages")
                if isinstance(messages, list) and all(isinstance(message, dict) for message in messages):
                    st.html('<div class="df-quality-preview"><div class="bubbles">'
                            + render_message_sequence(messages) + '</div></div>')
                else:
                    st.code(json.dumps(sample, ensure_ascii=False, indent=2, default=str)[:10000], language="json")
            else:
                st.info("问题记录中的样本 ID 未在当前文件中找到，请重新选择文件后检查。")
        elif code_counts:
            st.info("当前筛选条件下没有问题记录。")
    with st.expander("检查边界与原始问题码"):
        st.caption("本报告只验证结构和当前内容绑定的审核记录；含图像样本还需要具备视觉能力的人工复核。")
        st.json({"问题码计数": code_counts, "放量阻断码": data["block_reasons"]})


def page_monitor(show_title=True):
    if show_title:
        page_header("运行监控", "跟踪命令任务状态和最近输出，快速定位失败阶段。", "RUN MONITOR")
    path = _OUT("runs.jsonl")
    rows = []
    invalid = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(event, dict):
                rows.append(event)
            else:
                invalid += 1
    if invalid:
        st.warning(f"日志中有 {invalid} 条记录无法读取，以下只展示有效事件。")
    if rows:
        st.html('''<style>
        .df-event-summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:0 0 14px; }
        .df-event-summary > div { display:grid; gap:5px; min-height:82px; padding:14px 16px; border:1px solid #DFE8F4; border-radius:11px; background:#fff; }
        .df-event-summary span { color:#71839A; font-size:12px; }
        .df-event-summary strong { overflow:hidden; color:#1C3453; font-size:20px; text-overflow:ellipsis; white-space:nowrap; }
        .df-event-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px; margin:10px 0; }
        .df-event-facts > div { min-width:0; padding:10px 12px; border:1px solid #E3EBF6; border-radius:9px; background:#F9FBFF; }
        .df-event-facts span { display:block; color:#71839A; font-size:11px; }
        .df-event-facts strong { display:block; margin-top:4px; overflow-wrap:anywhere; color:#263C57; font-size:13px; }
        @media(max-width:800px) { .df-event-summary,.df-event-facts { grid-template-columns:1fr; } }
        </style>''')
        recent = list(reversed(rows))
        kinds = sorted({str(row.get("kind") or "未知类型") for row in recent})
        st.html('<div class="df-event-summary">'
                f'<div><span>记录总数</span><strong>{len(rows):,}</strong></div>'
                f'<div><span>事件类型</span><strong>{len(kinds):,}</strong></div>'
                '<div><span>最近记录</span><strong>'
                + html.escape(str(recent[0].get("at") or "—")[:19].replace("T", " "))
                + '</strong></div></div>')
        left, right = st.columns([1, 2], gap="large")
        with left, st.container(border=True):
            section_heading("事件时间线", "按类型筛选并选择一条记录", "◷")
            kind = st.selectbox("事件类型", ["全部类型", *kinds], key=f"monitor-kind:{st.session_state['ws']}")
            visible = [row for row in recent if kind == "全部类型" or str(row.get("kind") or "未知类型") == kind][:100]
            st.caption(f"显示最近 {len(visible)} / {len(rows)} 条")
            with st.container(height=510, border=False):
                selected = st.radio(
                    "选择事件", list(range(len(visible))), label_visibility="collapsed",
                    format_func=lambda index: (
                        f"{visible[index].get('kind') or '未知类型'} · "
                        f"{str(visible[index].get('at') or '时间未知')[:19].replace('T', ' ')}"
                    ), key=f"monitor-event:{st.session_state['ws']}:{kind}",
                )
        with right, st.container(border=True):
            section_heading("事件详情", "显示原始记录中的实际字段", "▤")
            event = visible[selected]
            facts = []
            for key, value in event.items():
                if key in ("kind", "at") or isinstance(value, (dict, list)):
                    continue
                facts.append('<div><span>' + html.escape(str(key)) + '</span><strong>'
                             + html.escape(str(value)[:200]) + '</strong></div>')
            if facts:
                st.html('<div class="df-event-facts">' + ''.join(facts) + '</div>')
            else:
                st.caption("该事件只有类型和时间，可展开查看原始记录。")
            with st.expander("查看原始事件记录"):
                st.json(event)
    else:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">◷</span><strong>当前工作区暂无运行日志</strong><p>任务启动后，这里会显示阶段、耗时与执行结果。</p></div>')
    _job_status()


def page_backends():
    from lib.presentation.streamlit.backend_page import render_backend_page

    render_backend_page()


def page_gates(show_title=True):
    if show_title:
        page_header("质量闸门", "查看生成、人工审核与发布前的确认要求。", "HUMAN IN THE LOOP")
    from lib.presentation.streamlit.settings_style import SETTINGS_STYLE

    st.html(SETTINGS_STYLE)
    gate = _gate()
    statuses = {gid: gate.status(gid) for gid in gate.defs}
    total = len(statuses)
    approved = sum(status == "approved" for status in statuses.values())
    attention = sum(status in ("awaiting", "rejected") for status in statuses.values())
    pending = sum(status == "pending" for status in statuses.values())
    st.html(
        '<div class="df-settings-section"><span><i>◇</i><span><strong>命令管线确认点</strong>'
        '<small>预算、数据来源与放量操作的人工确认记录</small></span></span>'
        f'<b>已确认 {approved} / {total}</b></div>'
        '<div class="df-settings-overview">'
        '<div class="df-settings-overview-main"><strong>确认进度</strong>'
        '<small>根据当前工作区状态与闸门定义实时汇总</small>'
        f'<div class="df-settings-progress"><i style="width:{approved / total * 100 if total else 0:.1f}%"></i></div></div>'
        f'<div class="df-settings-stat" data-kind="approved"><span>已确认</span><strong>{approved}</strong></div>'
        f'<div class="df-settings-stat" data-kind="attention"><span>需处理</span><strong>{attention}</strong></div>'
        f'<div class="df-settings-stat"><span>尚未触发</span><strong>{pending}</strong></div>'
        '</div>'
    )
    columns = st.columns(3, gap="medium")
    status_labels = {"approved": "已确认", "awaiting": "待确认", "pending": "尚未触发", "rejected": "未通过"}
    for column, (gid, definition) in zip(columns, gate.defs.items()):
        status = statuses[gid]
        record = gate.records.get(gid)
        context = record.context if record else {}
        prompt = re.sub(r"\{([a-zA-Z0-9_]+)\}",
                        lambda match: str(context.get(match.group(1), "待任务提供")), definition.prompt)
        with column, st.container(border=True, key=f"settings-gate-{gid}"):
            st.html('<div class="df-settings-gate-head" data-status="' + html.escape(status, quote=True) +
                    '"><b>' + html.escape(gid) + '</b><div><strong>' +
                    html.escape(definition.title) + '</strong><small>' + html.escape(definition.trigger) +
                    '</small></div><span data-status="' + html.escape(status, quote=True) + '">' +
                    html.escape(status_labels.get(status, status)) + '</span></div>')
            if status == "approved":
                confirmed_scope = (prompt if all(context.get(field) not in (None, "")
                                                 for field in definition.requires)
                                   else "此确认点已通过；当时的确认参数未保存在记录中。")
                st.html('<div class="df-settings-gate-prompt" data-status="approved">'
                        + html.escape(confirmed_scope) + '</div>')
                if record and record.decided_at:
                    st.html('<div class="df-settings-gate-time"><b>✓</b> 确认时间 ' +
                            html.escape(record.decided_at[:19].replace("T", " ")) + '</div>')
            else:
                st.html('<div class="df-settings-gate-prompt">' + html.escape(prompt) + '</div>')
                confirmation = st.checkbox("我已核对上述条件", key="confirm:" + st.session_state["ws"] + ":" + gid)
                if st.button("确认通过 " + gid, disabled=not confirmation, width="stretch"):
                    gate.decide(gid, True, note="控制台人工确认")
                    st.rerun()
    st.html('<div class="df-settings-footnote">这些闸门用于命令管线；自动数据工作流还有逐阶段质检与人工审核记录。'
            '审核汇总不会自动放行 G3，批量导出仍需校验当前样本的审核覆盖。</div>')


def _asset_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def page_assets(show_title=True):
    if show_title:
        page_header("数据管理", "浏览来源、对话样本、偏好数据和工作流产物。", "DATA LIBRARY")
    from datetime import datetime
    from lib.bootstrap.asset_catalog import asset_catalog_application
    from lib.domain.dataset_assets import DIRECT_DOWNLOAD_LIMIT_BYTES, TRAINING_CATEGORIES
    from lib.presentation.streamlit.data_management_style import DATA_MANAGEMENT_STYLE
    st.html(DATA_MANAGEMENT_STYLE)
    catalog = asset_catalog_application()
    workspace_id = st.session_state["ws"]
    inventory = catalog.inventory(workspace_id)
    categories = catalog.categories(inventory)
    source_count = categories.get("来源文件", 0)
    output_count = len(inventory.assets) - source_count
    training_count = sum(categories.get(category, 0) for category in TRAINING_CATEGORIES)
    preference_count = categories.get("DPO 偏好对", 0) + categories.get("其他偏好数据", 0)
    category_names = ["常用文件", "全部文件", "来源文件", *(
        category for category in categories if category != "来源文件")]
    more = "+" if inventory.truncated else ""
    st.html(
        '<div class="df-data-stats">'
        f'<div class="df-data-stat"><b>▤</b><span>来源文件</span><strong>{source_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◈</b><span>工作区产物</span><strong>{output_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◉</b><span>训练数据文件</span><strong>{training_count:,}{more}</strong></div>'
        f'<div class="df-data-stat"><b>◇</b><span>偏好文件</span><strong>{preference_count:,}{more}</strong></div>'
        '</div>'
    )
    if inventory.truncated:
        st.caption("文件扫描已达到安全上限，计数为当前已列出的数量；请缩小工作区范围查看其余文件。")
    filter_col, search_col = st.columns([1.4, 1], vertical_alignment="bottom")
    with filter_col:
        category = st.selectbox("文件分类", category_names, key=f"asset-category:{st.session_state['ws']}")
    with search_col:
        search = st.text_input("搜索文件", placeholder="按文件名或路径筛选…",
                               key=f"asset-search:{st.session_state['ws']}")
    filtered = catalog.select(inventory, category, search)
    selected_rows = filtered[:100]
    list_col, detail_col = st.columns([1.55, 1], gap="large")
    with detail_col:
        detail_panel = st.container(border=True)
    with detail_panel:
        section_heading("文件详情", "查看路径、大小和内容摘录", "◉")
        if selected_rows:
            selected = st.selectbox(
                "查看文件详情", selected_rows,
                format_func=lambda asset: asset.label,
                key=f"asset-selected:{st.session_state['ws']}:{category}:{search}",
            )
    with list_col, st.container(border=True):
        section_heading("文件目录", f"{len(filtered)} 个匹配文件 · 来源优先", "▤")
        if not filtered:
            st.html('<div class="df-empty-state"><span class="df-empty-state-icon">▤</span><strong>没有匹配的文件</strong><p>清除搜索词，或切换文件分类。</p></div>')
        else:
            rows = []
            for asset in selected_rows:
                ext = asset.suffix.upper().lstrip(".") or "FILE"
                label = asset.label
                size = _asset_size(asset.size)
                rows.append(
                    f'<div class="df-data-list-row" data-selected="{str(asset == selected).lower()}">'
                    f'<div class="df-data-file"><b data-ext="{html.escape(ext, quote=True)}">{html.escape(ext[:5])}</b>'
                    f'<span title="{html.escape(label, quote=True)}">{html.escape(asset.name)}</span></div>'
                    f'<span>{html.escape(ext)}</span>'
                    f'<span title="{html.escape(label, quote=True)}">{html.escape(label)}</span>'
                    f'<span>{html.escape(size)}</span></div>'
                )
            st.html('<div class="df-data-list"><div class="df-data-list-head"><span>文件名</span><span>类型</span><span>来源 / 目录</span><span>大小</span></div>' + "".join(rows) + '</div>')
            if len(filtered) > len(selected_rows):
                st.caption(f"为保持浏览流畅，当前显示前 {len(selected_rows)} 个文件；请使用搜索缩小范围。")
    with detail_panel:
        if not filtered:
            st.caption("选择一个文件后，可在此查看详情并下载。")
        else:
            origin = selected.origin
            st.html(
                '<div class="df-data-detail">'
                f'<div><span>文件</span><strong>{html.escape(selected.name)}</strong></div>'
                f'<div><span>位置</span><strong>{html.escape(selected.label)}</strong></div>'
                f'<div><span>来源</span><strong class="df-data-origin" data-origin="{origin}">{"已有资料" if origin == "source" else "工作区产物"}</strong></div>'
                f'<div><span>大小</span><strong>{_asset_size(selected.size)}</strong></div>'
                f'<div><span>修改时间</span><strong>{datetime.fromtimestamp(selected.mtime_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")}</strong></div>'
                '</div>'
            )
            if selected.size <= DIRECT_DOWNLOAD_LIMIT_BYTES:
                try:
                    payload = catalog.download(workspace_id, selected)
                    st.download_button("下载所选文件", payload, file_name=selected.name,
                                       key=f"asset-download-button:{workspace_id}:{selected.id}", width="stretch")
                except (OSError, ValueError) as error:
                    st.warning(f"文件已变化或无法下载：{error}")
            else:
                st.caption("文件超过 50 MiB；请从工作区目录直接读取，或在输出打包中下载任务数据包。")
            try:
                excerpt, note = catalog.excerpt(workspace_id, selected)
                st.caption("内容摘录")
                if excerpt:
                    st.code(excerpt, language="json" if selected.suffix in (".json", ".jsonl") else "text")
                st.html('<p class="df-data-note">' + html.escape(note) + '</p>')
            except (OSError, ValueError) as error:
                st.warning(f"文件已变化或无法预览：{error}")


def page_prefs(show_title=True):
    from lib.presentation.streamlit.generation_settings_page import render_generation_settings

    render_generation_settings(ROOT, show_title=show_title)


def page_data_management():
    page_header("数据管理", "统一浏览来源与产物，预览各类训练样本并查看质量检查结果。", "DATA LIBRARY")
    area = st.segmented_control(
        "数据视图", ("资产管理", "数据预览", "质量报告"),
        default="资产管理", key=f"data-view:{st.session_state['ws']}",
        label_visibility="collapsed",
    )
    if area == "数据预览":
        page_preview(show_title=False)
    elif area == "质量报告":
        page_quality(show_title=False)
    else:
        page_assets(show_title=False)


def page_task_manager():
    page_header("任务管理", "查看自动工作流进度、命令运行状态与最近事件。", "TASK CENTER")
    area = st.segmented_control(
        "任务视图", ("数据工作流", "命令管线", "运行日志"),
        default="数据工作流", key=f"task-view:{st.session_state['ws']}",
        label_visibility="collapsed",
    )
    if area == "运行日志":
        page_monitor(show_title=False)
    elif area == "命令管线":
        page_run(show_title=False)
    else:
        from lib.bootstrap.workflows import workflow_application
        from lib.presentation.streamlit.task_management_page import render_task_management
        application = workflow_application(ROOT, _ws_out())
        render_task_management(
            application, st.session_state["ws"], _begin,
            lambda: _select_page("自动工作流"),
        )


def page_system_settings():
    page_header("系统设置", "管理质量确认点与生成偏好，控制数据进入审核和导出之前的检查。", "WORKSPACE SETTINGS")
    from lib.presentation.streamlit.settings_style import SETTINGS_STYLE

    st.html(SETTINGS_STYLE)
    with st.container(border=True):
        section_heading("界面语言", "选择控制台显示语言。", "Aa")
        st.selectbox(
            "界面语言", ["简体中文", "English"], key="ui-language-choice",
            label_visibility="collapsed", on_change=_set_ui_language,
        )
    area = st.segmented_control(
        "系统设置视图", ("HITL 闸门", "生成偏好"),
        default="HITL 闸门", key="system-settings-view",
        label_visibility="collapsed",
    )
    if area == "生成偏好":
        page_prefs(show_title=False)
    else:
        page_gates(show_title=False)


PAGES = {
    "首页": page_overview, "总览": page_overview,
    "数据生成": page_workflow, "自动工作流": page_workflow,
    "数据管理": page_data_management, "资产管理": page_assets, "数据预览": page_preview,
    "人工审核": page_human_review, "任务管理": page_task_manager, "管线运行": page_run,
    "运行监控": page_monitor, "监控": page_monitor, "输出打包": page_output_packages, "质量报告": page_quality,
    "模型与密钥": page_backends, "系统设置": page_system_settings,
    "闸门": page_gates, "偏好设置": page_prefs,
}
try:
    _workspace_options, _previous_workspace = _ws_choice()
except (ValueError, OSError) as error:
    st.error(str(error))
    st.stop()
# A newly started workflow should open at its live task graph.  Consume the
# handoff before the nav radio exists; Streamlit forbids changing a widget's
# session value after it has been instantiated in the current render.
_workflow_handoff = st.session_state.pop("workflow-open-run", None)
if (isinstance(_workflow_handoff, dict)
        and _workflow_handoff.get("workspace") == st.session_state["ws"]
        and _workflow_handoff.get("run_id")):
    _handoff_run_id = str(_workflow_handoff["run_id"])
    st.session_state["nav"] = "任务管理"
    st.session_state[f"task-view:{st.session_state['ws']}"] = "数据工作流"
    st.session_state[f"task-center-run:{st.session_state['ws']}"] = _handoff_run_id
    st.session_state["workflow-scroll-top"] = True
# 深链：?page=人工审核&record=<sample_id>（协作者可直接分享定位链接）
_qp_page = st.query_params.get("page")
_review_route = {"偏好审核": "DPO 偏好优化", "语料审核": "CPT 语料审核"}
if _qp_page in _review_route:
    st.session_state["nav"] = "人工审核"
    st.session_state[f"review-mode:{st.session_state['ws']}"] = _review_route[_qp_page]
if _qp_page in PAGES and "nav" not in st.session_state:
    st.session_state["nav"] = _qp_page
visible_nav = [
    ("首页", "总览", {"首页", "总览"}),
    ("数据生成", "自动工作流", {"数据生成", "自动工作流"}),
    ("数据管理", "数据管理", {"数据管理", "资产管理", "数据预览", "质量报告"}),
    ("人工审核", "人工审核", {"人工审核"}),
    ("任务管理", "任务管理", {"任务管理", "管线运行", "运行监控", "监控"}),
    ("输出打包", "输出打包", {"输出打包"}),
    ("模型服务", "模型与密钥", {"模型与密钥"}),
    ("系统设置", "系统设置", {"系统设置", "闸门", "偏好设置"}),
]
icons = {"首页": "⌂", "数据生成": "◈", "数据管理": "▤", "人工审核": "✓",
         "任务管理": "⤴", "输出打包": "⇩", "模型与密钥": "⬡", "模型服务": "⬡", "系统设置": "⚙"}
if "nav" not in st.session_state:
    st.session_state["nav"] = _qp_page if _qp_page in PAGES else "总览"
# Older sessions could retain a formatted label from the former hidden radio
# (for example, "⌂ Home") instead of a route key. Resolve it before dispatch.
_route_label = canonical_navigation_route(
    st.session_state.get("nav", "总览"), PAGES, icons,
)
if st.session_state.get("nav") != _route_label:
    st.session_state["nav"] = _route_label
page = _route_label
for label, target, active_pages in visible_nav:
    st.sidebar.button(
        f"{icons.get(label, '•')}　{label}",
        key=f"nav-button:{target}",
        type="primary" if page in active_pages else "secondary",
        on_click=_select_page,
        args=(target,),
        width="stretch",
    )
st.sidebar.divider()
st.sidebar.caption("当前工作区")
st.sidebar.selectbox("工作区", _workspace_options,
                     format_func=lambda ws: WORKSPACES.label(ws), key="ws", label_visibility="collapsed")
st.sidebar.button("打开已有文件夹…", on_click=_request_folder_dialog, width="stretch")
try:
    with st.sidebar.expander("文件夹位置"):
        st.code(WORKSPACES.folder(st.session_state['ws']).as_posix(), language=None)
except FileNotFoundError as error:
    st.sidebar.warning(str(error))
st.sidebar.html('<div class="df-sidebar-note"><span class="df-note-mark">✦</span><strong>从资料到训练数据</strong><small>自动生成 · 全程可追溯</small></div>')
st.query_params['ws'] = st.session_state['ws']
if st.session_state['ws'] != _previous_workspace:
    st.query_params.pop('record', None)
st.session_state['last-workspace'] = st.session_state['ws']
top_page = translate_label(page, st.session_state.get("ui_language", "zh"))
st.html(f'<div class="df-topbar"><div><strong>数简立方</strong><span>　/　{html.escape(top_page)}</span></div><div class="df-topbar-meta">当前工作区　<strong>{html.escape(WORKSPACES.label(st.session_state["ws"]))}</strong>　·　数据简单生成</div></div>')
st.query_params['page'] = page
try:
    PAGES[page]()
except (ValueError, OSError) as error:
    st.error(str(error))
if st.session_state.get('open-folder-dialog'):
    _open_folder_dialog()
