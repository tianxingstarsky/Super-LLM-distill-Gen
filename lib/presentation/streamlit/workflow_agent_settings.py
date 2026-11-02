"""Choose imported-trajectory editing or replay inside the existing Agent node."""
import streamlit as st


def _tr(zh: str, en: str) -> str:
    return en if st.session_state.get("ui_language") == "en" else zh


def agent_optimization_snapshot(workspace: str) -> bool:
    key = f"workflow-agent-trajectory-optimization:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    return st.session_state.get(key, draft.get(key, False)) is True


def initialize_agent_processing(workspace: str, *, save_field) -> None:
    """New empty drafts opt in; unmarked saved drafts retain their old behavior."""
    key = f"workflow-agent-trajectory-optimization:{workspace}"
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    if key not in st.session_state:
        st.session_state[key] = draft.get(key, not bool(draft)) is True
    if key not in draft:
        save_field(workspace, key)


def _save_processing(workspace: str, save_field) -> None:
    key = f"workflow-agent-trajectory-optimization:{workspace}"
    st.session_state[key] = st.session_state[f"workflow-agent-processing-choice:{workspace}"] == "optimize"
    save_field(workspace, key)


def render_agent_processing(workspace: str, capabilities: dict, check_environment=None, *, save_field) -> bool:
    """Expose the frozen processing choice without adding a page or a scheduler."""
    optimizing = agent_optimization_snapshot(workspace)
    choice_key = f"workflow-agent-processing-choice:{workspace}"
    st.session_state[choice_key] = "optimize" if optimizing else "replay"
    st.radio(_tr("轨迹处理方式", "Trajectory processing"), ("optimize", "replay"),
             format_func=lambda value: _tr("流程与 CoT 优化", "Flow and CoT optimization") if value == "optimize"
                 else _tr("工具重放验证", "Tool replay verification"),
             key=choice_key, horizontal=True, on_change=_save_processing, args=(workspace, save_field))
    if optimizing:
        st.caption(_tr("每条轨迹默认一次作者调用，只编辑最终助手的 CoT。最终答案、其余推理、工具参数、返回和顺序保持原样。",
                       "One author call per trajectory by default. Only the final assistant CoT is edited. The final answer, other reasoning, tool arguments, observations and order stay unchanged."))
        st.caption(_tr("流程建议单独保存；需要新工具或改参数的建议不算已执行轨迹。无需在本机重放工具。",
                       "Flow suggestions are saved separately. Suggestions requiring new tools or changed arguments are not executed traces. Local tool replay is not required."))
        st.caption(_tr("缺失推理仍按输入节点的保留／补写／排除选择处理。保留模式下，没有可编辑推理时不调用作者。",
                       "Missing reasoning follows the input node’s preserve, fill or exclude policy. Preserve mode skips the author when there is no reasoning to edit."))
        st.caption(_tr("完整提示词与长期经验在本节点「提示词与风格」中配置，启动时固定版本。",
                       "Configure the full prompt and long-term experience in this node’s Prompts and style tab. Their versions are frozen when the run starts."))
    else:
        from lib.presentation.streamlit.workflow_node_settings import render_agent_verification
        render_agent_verification(workspace, capabilities, check_environment)
    return optimizing
