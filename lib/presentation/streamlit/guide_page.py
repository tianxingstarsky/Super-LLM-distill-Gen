"""Workspace-aware guide to the console's main user journey."""
from __future__ import annotations

import html
from typing import Callable

import streamlit as st

from lib.presentation.streamlit.guide_style import GUIDE_STYLE
from lib.presentation.streamlit.i18n import translate
from lib.presentation.streamlit.shared import page_header, section_heading


_SOURCE_GUIDES = {
    "文档资料": (
        "上传 PDF、DOCX 或文本资料，选择 CPT、SFT 等目标。",
        ("保留原文位置", "解析与清洗", "生成训练候选"),
    ),
    "Agent 上下文": (
        "导入对话与工具调用记录，检查轨迹后生成训练候选。",
        ("导入上下文", "核对工具轨迹", "查看失败片段"),
    ),
    "开放需求": (
        "直接描述任务、场景和质量要求，无需先上传文件。",
        ("描述需求", "选择训练目标", "分批生成候选"),
    ),
}


def render_guide(workspace_id: str, workspace_label: str, *, has_source_files: bool,
                 navigate: Callable[[str], None]) -> None:
    """Show navigation and honest local context without changing source data."""
    page_header("使用指南", "从输入到交付，按步骤完成一套可审查的训练数据。", "GETTING STARTED")
    st.html(GUIDE_STYLE)
    displayed_label = (translate(workspace_label, st.session_state.get("ui_language", "zh"))
                       if workspace_id == "default" else workspace_label)
    st.html('<div class="df-guide-workspace"><strong>当前工作区</strong>'
            '<b data-user-content>' + html.escape(displayed_label) + '</b><span>'
            + ("已有来源文件" if has_source_files else "暂无来源文件；开放需求可直接开始")
            + '</span></div>')

    choice = st.segmented_control(
        "选择你的起点", tuple(_SOURCE_GUIDES), default="文档资料" if has_source_files else "开放需求",
        key=f"guide-source:{workspace_id}",
    ) or ("文档资料" if has_source_files else "开放需求")
    description, hints = _SOURCE_GUIDES[choice]

    left, right = st.columns([1.15, 1], gap="medium")
    with left, st.container(border=True, key="guide-source-card"):
        section_heading("准备输入", "选择适合当前资料的起点。", "▤")
        st.write(description)
        st.html('<div class="df-guide-route">' + ''.join(
            '<span>' + html.escape(hint) + '</span>' for hint in hints
        ) + '</div>')
        if st.button("按此方式开始生成 →", key="guide-start", type="primary", width="stretch"):
            st.session_state[f"workflow-source-mode:{workspace_id}"] = choice
            navigate("自动工作流")
            st.rerun()

    with right, st.container(border=True, key="guide-model-card"):
        section_heading("配置模型", "服务连接与任务选型分两步完成。", "◇")
        st.html('<div class="df-guide-help">'
                '<div><b>1</b><span><strong>登记服务连接</strong>：设置服务地址、凭据和预算。</span></div>'
                '<div><b>2</b><span><strong>在工作流节点选模型</strong>：点击需要模型的节点，分别选择生成与评审模型。</span></div>'
                '</div>')
        service, workflow = st.columns(2, gap="small")
        service.button("查看模型服务", key="guide-services", on_click=navigate,
                       args=("模型与密钥",), width="stretch")
        workflow.button("进入节点配置", key="guide-nodes", on_click=navigate,
                        args=("自动工作流",), width="stretch")

    st.html('<div class="df-guide-flow">'
            '<div><b>01</b><strong>输入与目标</strong><small>资料、轨迹或开放需求</small></div>'
            '<div><b>02</b><strong>节点模型</strong><small>按步骤选择服务与模型</small></div>'
            '<div><b>03</b><strong>运行与质检</strong><small>查看阶段进度和失败原因</small></div>'
            '<div><b>04</b><strong>人工审核</strong><small>核对样本和 Agent 轨迹</small></div>'
            '<div><b>05</b><strong>校验与打包</strong><small>确认文件后导出</small></div>'
            '</div>')

    destinations = (
        ("跟踪任务", "查看每个节点的运行过程、质量结果与失败记录。", "任务管理", "打开任务中心"),
        ("审查样本", "逐条核对来源、回答与 Agent 轨迹，再做人工决定。", "人工审核", "进入人工审核"),
        ("交付数据", "核对实际产物和完整性，生成可下载的数据包。", "输出打包", "查看输出打包"),
    )
    for column, (title, detail, route, action) in zip(st.columns(3, gap="medium"), destinations):
        with column, st.container(border=True, key=f"guide-step-{route}"):
            section_heading(title, detail, "◷" if route == "任务管理" else "✓" if route == "人工审核" else "⇩")
            st.button(action, key=f"guide-open-{route}", on_click=navigate,
                      args=(route,), width="stretch")

    st.html('<div class="df-guide-settings">生成偏好与人工确认点可在系统设置中调整；'
            '每次任务使用的模型仍由工作流节点决定。</div>')
    st.button("打开系统设置", key="guide-settings", on_click=navigate,
              args=("系统设置",), width="stretch")
