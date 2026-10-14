"""Expert experience stays editable beside the processing step that uses it."""
import streamlit as st

from lib.domain.agent_experience import default_experience_text, experience_scope, validate_agent_experience
from lib.presentation.streamlit.prompt_library_controls import render_prompt_library

SCOPES = ("director", "question", "answer", "reasoning", "review", "cpt")
LABELS = {"director": "对话设计", "question": "提问", "answer": "回答",
          "reasoning": "推理", "review": "评审", "cpt": "语料清洗"}


def _field(workspace, key, default):
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(
        key, st.session_state.get(key, default))


def experience_configuration(workspace):
    return {scope: {"text": _field(workspace, f"workflow-agent-experience-text:{workspace}:{scope}",
                                  default_experience_text(scope)),
                    "enabled": _field(workspace, f"workflow-agent-experience-enabled:{workspace}:{scope}", True)}
            for scope in SCOPES}


def experience_has_issue(config):
    try:
        validate_agent_experience(config)
    except (ValueError, TypeError):
        return True
    return False


def invalid_experience_scopes(config):
    return [scope for scope in SCOPES if experience_has_issue({scope: config[scope]})]


def _restore_experience(workspace, scope, save_field):
    from lib.presentation.streamlit.prompt_library_controls import prompt_library_key

    for field, value in (("text", default_experience_text(scope)), ("enabled", True)):
        key = f"workflow-agent-experience-{field}:{workspace}:{scope}"
        st.session_state[key] = value
        save_field(workspace, key)
    st.session_state[prompt_library_key(workspace, f"experience:{scope}", "selection")] = None


def render_inactive_experience_issue(workspace, scope, *, save_field):
    st.caption(LABELS[scope])
    st.warning("未启用步骤的经验内容无效，可恢复这份经验后继续。")
    st.button("恢复这份经验", key=f"workflow-experience-recover:{workspace}:{scope}",
              on_click=_restore_experience, args=(workspace, scope, save_field))


def render_experience(workspace, prompt_id, *, save_field, prompt_library):
    scope = experience_scope(prompt_id)
    if scope is None:
        return
    text_key = f"workflow-agent-experience-text:{workspace}:{scope}"
    enabled_key = f"workflow-agent-experience-enabled:{workspace}:{scope}"
    default = default_experience_text(scope)
    st.session_state[text_key] = _field(workspace, text_key, default)
    st.session_state[enabled_key] = _field(workspace, enabled_key, True)
    # An occasional long-form editor does not expand the canvas/node window.
    with st.popover("长期经验", width="stretch"):
        st.caption(LABELS[scope])
        st.caption("预存处理经验、适用条件与正反例。按当前步骤加入系统指令；不会把历史答案当作知识。")
        st.caption("同类角色共用这份经验；节点完整模板仍各自保存。")
        st.toggle("启用长期经验", key=enabled_key, on_change=save_field, args=(workspace, enabled_key))
        render_prompt_library(prompt_library, workspace, f"experience:{scope}",
                              fields={"text": text_key, "enabled": enabled_key},
                              defaults={"text": default, "enabled": True}, save_field=save_field,
                              label="我的经验模板", save_label="保存经验")
        st.text_area("经验正文", key=text_key, height=300, max_chars=32768,
                     on_change=save_field, args=(workspace, text_key))
        if experience_has_issue({scope: {"text": st.session_state[text_key], "enabled": st.session_state[enabled_key]}}):
            st.error("长期经验内容无效，请恢复默认或使用不超过 32 KiB 的 UTF-8 文本。")
    st.caption("长期经验已启用 · 可编辑、保存与恢复默认" if st.session_state[enabled_key] else "长期经验已关闭")


def render_pinned_experience(recipe, prompt_id, run_id):
    if recipe.get("agent_experience_version") != 1:
        return
    scope = experience_scope(prompt_id)
    if scope is None:
        return
    pinned = recipe.get("agent_experience", {}).get("scopes", {}).get(scope, {})
    if pinned.get("enabled") is not True:
        st.caption("长期经验已关闭")
        return
    with st.popover("长期经验", width="stretch"):
        st.caption("本次任务已固定的经验；复制为新草稿后可以修改。")
        st.text_area("经验正文", value=pinned.get("text", ""), height=300, disabled=True,
                     key=f"workflow-run-experience:{run_id}:{scope}")
