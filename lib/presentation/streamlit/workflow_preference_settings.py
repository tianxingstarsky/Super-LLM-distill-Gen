"""Contrastive design controls live on the preference node."""
import streamlit as st
from lib.contexts.workflows.contracts import validate_preference_design
from lib.presentation.streamlit.i18n import translate_label


def _value(workspace, name, default):
    key = f"workflow-preference-{name}:{workspace}"
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(key, st.session_state.get(key, default))


def preference_design_snapshot(workspace):
    return validate_preference_design({"mode": _value(workspace, "mode", "off_topic_short"),
        "negative_reasoning_max_chars": int(_value(workspace, "negative-chars", 160)),
        "negative_reasoning_max_ratio": float(_value(workspace, "negative-ratio", 25)) / 100})


def render_preference_settings(workspace, *, save_field):
    st.markdown("**" + translate_label("负例设计", st.session_state.get("ui_language", "zh")) + "**")
    key = f"workflow-preference-mode:{workspace}"
    st.session_state[key] = _value(workspace, "mode", "off_topic_short")
    st.radio("负例类型", ("off_topic_short", "opposite_short"), horizontal=True, key=key,
        format_func=lambda mode: "答非所问＋短推理" if mode == "off_topic_short" else "相反结论＋短推理",
        on_change=save_field, args=(workspace, key))
    st.caption("正例保留充分推导，负例使用所选偏差与极短推理。双方独立评分后，共用一份结果导出 DPO、ORPO 和 RLAIF。")
    left, right = st.columns(2)
    for column, name, label, default, minimum, maximum in (
        (left, "negative-chars", "负例推理字符上限", 160, 20, 1000),
        (right, "negative-ratio", "负例与正例推理长度比上限（%）", 25, 5, 50),
    ):
        key = f"workflow-preference-{name}:{workspace}"
        st.session_state[key] = int(_value(workspace, name, default))
        column.number_input(label, min_value=minimum, max_value=maximum, step=5, key=key,
            on_change=save_field, args=(workspace, key))
    st.caption("短推理仍保留 reasoning_content。生成与负例类型核验模板可在本节点的提示词中修改；正确性分差要求保持不变。")
