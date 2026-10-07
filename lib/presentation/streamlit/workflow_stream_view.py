"""Display bounded, durable model output inside the selected workflow node."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.i18n import UntranslatedText, translate
from lib.presentation.streamlit.shared import section_heading


_STATUSES = {"active": "正在接收", "interrupted": "已中断，可重试", "completed": "接收完成"}
_ROLES = {"generation": "生成模型", "jev": "独立质量评审模型"}


def render_stream_output(application, run_id: str, stage: str) -> None:
    reader = getattr(application, "read_streams", None)
    if reader is None:
        return
    try:
        requests = [row for row in reader(run_id) if row.get("stage") == stage]
    except (OSError, ValueError, KeyError):
        st.caption("实时输出暂时无法读取；已完成的样本断点仍会保留。")
        return
    if not requests:
        return
    language = st.session_state.get("ui_language", "zh")
    key = f"workflow-stream:{run_id}:{stage}"
    with st.container(border=True, key=key):
        st.html('''<style>
        [class*="st-key-workflow-stream"] [data-testid="stCode"] pre {
          max-height:260px; overflow:auto;
        }
        </style>''')
        section_heading("实时模型输出", "显示当前节点已收到的内容；尚未通过完整性与质量检查。", "◉")
        latest = next((row for row in requests if row.get("status") == "active"), requests[0])
        if len(requests) > 1:
            follow = st.toggle("跟随最新输出", value=True, key=key + ":follow")
            if not follow:
                by_id = {row["id"]: row for row in requests}
                if st.session_state.get(key + ":request") not in by_id:
                    st.session_state.pop(key + ":request", None)

                def label(request_id):
                    row = by_id[request_id]
                    role = translate(_ROLES.get(row.get("role"), "模型请求"), language)
                    status = translate(_STATUSES.get(row.get("status"), "正在接收"), language)
                    return UntranslatedText(f"{role} · {row.get('unit', '')} · {status}")

                selected = st.selectbox("查看生成请求", list(by_id), format_func=label,
                                        key=key + ":request")
                latest = by_id[selected]
        status = latest.get("status", "active")
        st.caption(_STATUSES.get(status, "正在接收"))
        role = translate(_ROLES.get(latest.get("role"), "模型请求"), language)
        st.caption(UntranslatedText(f"{role} · {latest.get('unit', '')} · {latest.get('updated_at', '')}"))
        text_chars = latest.get("text_chars") or len(latest.get("text", ""))
        reasoning_chars = latest.get("reasoning_chars") or len(latest.get("reasoning", ""))
        st.caption(UntranslatedText(
            f"Received text: {text_chars:,} characters · Reasoning: {reasoning_chars:,} characters"
            if language == "en" else
            f"已接收正文 {text_chars:,} 字符 · 推理 {reasoning_chars:,} 字符"))
        if latest.get("text"):
            st.code(UntranslatedText(latest["text"]), language="json", height="content", wrap_lines=True)
        else:
            st.caption("等待首段正文…")
        if latest.get("reasoning"):
            with st.expander("模型返回的推理片段", expanded=not bool(latest.get("text"))):
                st.code(UntranslatedText(latest["reasoning"]), language=None,
                        height="content", wrap_lines=True)
        if latest.get("truncated"):
            st.caption("预览仅显示最近的片段；完整成功响应按原有断点保存。")
        if status == "interrupted":
            st.warning("此片段未进入训练数据。重试当前样本时会重新请求，已完成样本会复用断点。")
