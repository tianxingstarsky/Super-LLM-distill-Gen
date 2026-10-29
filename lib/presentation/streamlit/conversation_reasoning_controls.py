"""Choose how imported assistant turns with no reasoning enter a new run."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.i18n import translate_label


REASONING_POLICY_LABELS = {
    "preserve": "保留原样",
    "fill_missing": "仅补缺失推理",
    "exclude": "排除缺失记录",
}


def conversation_reasoning_snapshot(workspace: str) -> dict:
    """Project a saved choice without enabling generation in older drafts."""
    key = f"workflow-conversation-reasoning:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    mode = st.session_state.get(key, draft.get(key, "preserve"))
    return {"version": 1, "mode": mode}


def render_conversation_reasoning(workspace: str, *, save_field) -> dict:
    key = f"workflow-conversation-reasoning:{workspace}"
    if key not in st.session_state:
        st.session_state[key] = conversation_reasoning_snapshot(workspace)["mode"]
    mode = st.selectbox(
        "缺失推理处理", tuple(REASONING_POLICY_LABELS), key=key,
        format_func=lambda value: translate_label(
            REASONING_POLICY_LABELS[value], st.session_state.get("ui_language", "zh")),
        on_change=save_field, args=(workspace, key),
        help="逐个检查助手回合，也检查只有工具调用的回合。原始导入文件始终保留。",
    )
    descriptions = {
        "preserve": "保留已有推理，缺失处留空。可用于普通 SFT；需要完整推理的目标仍按原规则检查。",
        "fill_missing": "只补空缺的助手推理，保留原答案、工具记录和已有推理。补充内容标为合成解释，不是恢复原始思考。",
        "exclude": "任一助手回合缺失推理，就跳过整段记录；不会截断多轮对话或调用模型补充。",
    }
    st.caption(descriptions[mode])
    if mode == "fill_missing":
        st.caption("使用本节点的模型和提示词，按当时可见上下文逐轮补充。后续节点的风格改写仍独立生效。")
    return {"version": 1, "mode": mode}
