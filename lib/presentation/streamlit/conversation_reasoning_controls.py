"""Choose how imported assistant turns with no reasoning enter a new run."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.i18n import translate_label
from lib.prompts import get, render


REASONING_POLICY_LABELS = {
    "preserve": "保留原样",
    "fill_missing": "仅补缺失推理",
    "exclude": "排除缺失记录",
}


def conversation_reasoning_snapshot(workspace: str) -> dict:
    """Project a saved choice without enabling generation in older drafts."""
    key = f"workflow-conversation-reasoning:{workspace}"
    version_key = f"workflow-conversation-reasoning-version:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    mode = st.session_state.get(key, draft.get(key, "preserve"))
    # An existing choice without a version was saved under the v1 contract.
    version = st.session_state.get(version_key, draft.get(version_key, 1 if key in draft else 3))
    return {"version": version, "mode": mode}


def _save_reasoning_policy(workspace: str, save_field) -> None:
    if conversation_reasoning_snapshot(workspace)["version"] in {1, 2}:
        draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
        migrations = {
            "workflow.conversation_reasoning": (("1.0.0", "1.1.0", "2.0.0"), "2.1.0"),
            "workflow.conversation_reasoning_hints": (("1.0.0",), "1.1.0"),
        }
        for prompt_id, (old_versions, new_version) in migrations.items():
            prompt_key = f"workflow-node-prompt:{workspace}:ingest:{prompt_id}"
            body = st.session_state.get(prompt_key, draft.get(prompt_key))
            # Only this explicit policy change replaces known built-ins.
            # Custom text and templates frozen in existing runs stay exact.
            if body in {render(get(prompt_id, version=version)) for version in old_versions}:
                st.session_state[prompt_key] = render(get(prompt_id, version=new_version))
                save_field(workspace, prompt_key)
    version_key = f"workflow-conversation-reasoning-version:{workspace}"
    st.session_state[version_key] = 3
    save_field(workspace, version_key)
    save_field(workspace, f"workflow-conversation-reasoning:{workspace}")


def render_conversation_reasoning(workspace: str, *, save_field) -> dict:
    key = f"workflow-conversation-reasoning:{workspace}"
    policy = conversation_reasoning_snapshot(workspace)
    if key not in st.session_state:
        st.session_state[key] = policy["mode"]
    mode = st.selectbox(
        "缺失推理处理", tuple(REASONING_POLICY_LABELS), key=key,
        format_func=lambda value: translate_label(
            REASONING_POLICY_LABELS[value], st.session_state.get("ui_language", "zh")),
        on_change=_save_reasoning_policy, args=(workspace, save_field),
        help="逐个检查助手回合，也检查只有工具调用的回合。原始导入文件始终保留。",
    )
    descriptions = {
        "preserve": "保留已有推理，缺失处留空。可用于普通 SFT；需要完整推理的目标仍按原规则检查。",
        "fill_missing": "待完善（实验性）：可为缺失推理补写正文解释，保留原答案、工具记录和已有推理。补写解释尚未保证与原答案一致，不能视为恢复真实推理或自动质量通过。",
        "exclude": "任一助手回合缺失推理，就跳过整段记录；不会截断多轮对话或调用模型补充。",
    }
    st.caption(descriptions[mode])
    if mode == "fill_missing":
        if policy["version"] in {2, 3}:
            st.caption("正文 CoT 补写使用本节点已选模型：先从当前答案提炼推导提示，再根据当时可见上下文和提示补写正文。两步模板均可编辑。")
            st.caption("程序将补写正文保存到推理字段；本次调用的上游原生推理保留在请求证据中，不混入补写内容。已有原生推理仍原样保留。")
            if policy["version"] == 3:
                st.caption("程序按当前回合区分工具调用决策与文本回答；工具调用只补写调用依据，不要求提前知道工具结果。")
        else:
            st.caption("沿用已保存的补充方式与提示词，按当时可见上下文逐轮补充。")
        if policy["version"] in {1, 2}:
            st.button("使用新版正文 CoT", key=f"workflow-conversation-reasoning-upgrade:{workspace}",
                      on_click=_save_reasoning_policy, args=(workspace, save_field),
                      help="仅升级当前草稿中的内置模板，保留自定义正文；已启动任务仍使用原来的冻结配置。")
    return {**policy, "mode": mode}
