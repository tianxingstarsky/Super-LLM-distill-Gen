"""Dataset browsing presentation with explicit workflow and file-selection inputs."""
import html
import streamlit as st
from lib.application.workflow_service import WorkflowApplication
from lib.presentation.streamlit.shared import page_header, section_heading
from lib.domain.workflow_quality import tool_error_flag


def _message_tool_errors(message):
    records = [message]
    content = message.get("content")
    if isinstance(content, list):
        records.extend(block for block in content
                       if isinstance(block, dict) and block.get("type") == "tool_result")
    states = [tool_error_flag(record) for record in records]
    return any(failed for failed, _ in states), any(issue for _, issue in states)


def _move_sample(key, delta):
    st.session_state[key] += delta


def render_dataset_preview(workflow_app: WorkflowApplication, workspace_id: str, selected_samples, show_title=True):
    if show_title:
        page_header("数据预览", "浏览已校验的语料、对话、偏好对与工具轨迹。", "SAMPLE PREVIEW")
    from lib.presentation.streamlit.data_management_style import DATA_MANAGEMENT_STYLE
    st.html(DATA_MANAGEMENT_STYLE)
    from lib.presentation.streamlit.dataset_preview_page import render_workflow_samples
    verified_runs = [row for row in workflow_app.task_runs()
                     if row.get("status") in {"completed", "needs_attention"} and row.get("id")]
    if verified_runs:
        view = st.segmented_control(
            "预览来源", ("工作流产物", "已有对话文件"), default="工作流产物",
            key=f"preview-source:{workspace_id}", label_visibility="collapsed",
        )
        if view == "工作流产物":
            render_workflow_samples(workflow_app, workspace_id)
            return
    left, right = st.columns([1, 2.15], gap="large")
    with left, st.container(border=True):
        section_heading("选择样本", "从当前工作区已有的对话文件浏览", "▤")
        try:
            source, samples = selected_samples("preview-file")
        except (OSError, ValueError) as error:
            st.error(f"无法预览当前文件：{error}")
            return
        if samples:
            position_key = f"file-preview-position:{workspace_id}:{source}"
            positions = st.session_state.setdefault(f"file-preview-positions:{workspace_id}", {})
            st.session_state[position_key] = min(max(1, st.session_state.get(position_key, positions.get(str(source), 1))), len(samples))
            index = st.number_input("样本序号", 1, len(samples), step=1, key=position_key)
            positions[str(source)] = int(index)
            previous, following = st.columns(2, gap="small")
            previous.button("上一条", disabled=index <= 1, on_click=_move_sample,
                            args=(position_key, -1), key=f"file-preview-prev:{workspace_id}", width="stretch")
            following.button("下一条", disabled=index >= len(samples), on_click=_move_sample,
                             args=(position_key, 1), key=f"file-preview-next:{workspace_id}", width="stretch")
            st.caption(f"当前文件共 {len(samples):,} 条 · 正在查看第 {index:,} 条")
    if not samples:
        st.html('<div class="df-empty-state"><span class="df-empty-state-icon">◉</span><strong>还没有可预览的样本</strong><p>先选择已有数据文件，或从“数据生成”创建一条包含对话、推理或工具调用轨迹的工作流。</p></div>')
        return
    try:
        sample = samples[index - 1]
    except (OSError, ValueError) as error:
        st.error(f"无法预览当前文件：{error}")
        return
    messages = [message for message in sample.get("messages", []) if isinstance(message, dict)]
    from lib.presentation.streamlit.artifact_preview import (
        _tool_call_names, _tool_result_user, render_training_sample,
    )
    tool_calls = sum(len(_tool_call_names(message)) for message in messages)
    reasoning = sum(bool(message.get("reasoning_content") or message.get("reasoning"))
                    for message in messages)
    error_states = [_message_tool_errors(message) for message in messages]
    errors = sum(failed for failed, _ in error_states)
    invalid_flags = sum(invalid for _, invalid in error_states)
    user_turns = sum(message.get("role") == "user" and not _tool_result_user(message)
                     for message in messages)
    content_type = "Agent 工具轨迹" if tool_calls or any(message.get("role") == "tool" or _tool_result_user(message)
                                                   for message in messages) else "多轮对话" if user_turns > 1 else "问答样本"
    target = "agent" if content_type == "Agent 工具轨迹" else "multiturn" if user_turns > 1 else "sft"
    with left, st.container(border=True):
        section_heading("样本概览", "这些字段来自当前选中的记录", "◉")
        st.html(
            '<div class="df-data-sample-facts">'
            f'<div><span>样本 ID</span><b data-user-content>{html.escape(str(sample.get("id", index)))}</b></div>'
            f'<div><span>内容类型</span><b>{html.escape(content_type)}</b></div>'
            f'<div><span>消息 / 用户轮次</span><b>{len(messages)} / {user_turns}</b></div>'
            f'<div><span>工具调用</span><b>{tool_calls}</b></div>'
            f'<div><span>含推理记录</span><b>{reasoning}</b></div>'
            f'<div><span>工具错误</span><b>{errors}</b></div>'
            f'<div><span>无效工具标记</span><b>{invalid_flags}</b></div>'
            '</div>'
            '<p class="df-data-note">文件浏览仅展示原有样本内容；是否可用于训练请查看质量报告与人工审核。</p>'
        )
        st.caption("样本 ID: " + str(sample.get("id", index)))
    with right, st.container(border=True):
        section_heading("样本内容", "按对话轮次展开上下文与工具调用", "◉")
        st.html('<div class="df-data-sample-head"><strong data-user-content>' + html.escape(source.name if source else "样本")
                + '</strong><span>第 ' + str(index) + ' / ' + str(len(samples)) + ' 条</span></div>')
        st.html(render_training_sample(target, sample))


