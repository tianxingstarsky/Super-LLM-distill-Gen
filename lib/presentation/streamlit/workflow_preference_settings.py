"""Contrastive design controls live on the preference node."""
import streamlit as st
from lib.contexts.workflows.contracts import validate_preference_design
from lib.presentation.streamlit.i18n import translate_label


def _value(workspace, name, default):
    key = f"workflow-preference-{name}:{workspace}"
    return st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(key, st.session_state.get(key, default))


def preference_design_snapshot(workspace):
    return validate_preference_design({"mode": _value(workspace, "mode", "associative_drift_short"),
        "reuse_reviewed_failures": bool(_value(workspace, "reuse-failures", True)),
        "negative_reasoning_max_chars": int(_value(workspace, "negative-chars", 160)),
        "negative_reasoning_max_ratio": float(_value(workspace, "negative-ratio", 25)) / 100})


def render_preference_settings(workspace, *, save_field):
    st.markdown("**" + translate_label("负例设计", st.session_state.get("ui_language", "zh")) + "**")
    key = f"workflow-preference-mode:{workspace}"
    st.session_state[key] = _value(workspace, "mode", "associative_drift_short")
    st.radio("负例类型", ("associative_drift_short", "opposite_short", "off_topic_short"), horizontal=True, key=key,
        format_func=lambda mode: {"associative_drift_short": "错误联想渐进跑题", "off_topic_short": "完全无关话题（旧模式）",
                                  "opposite_short": "相反结论＋短推理"}[mode],
        on_change=save_field, args=(workspace, key))
    reuse_key = f"workflow-preference-reuse-failures:{workspace}"
    st.session_state[reuse_key] = bool(_value(workspace, "reuse-failures", True))
    st.checkbox("优先复用评审失败稿", key=reuse_key, on_change=save_field, args=(workspace, reuse_key))
    st.caption("已确认未解决问题且推理有缺漏的原稿可直接作负例，完整保留原文；没有合适原稿时，才按所选类型生成短推理负例。")
    left, right = st.columns(2)
    for column, name, label, default, minimum, maximum in (
        (left, "negative-chars", "负例推理字符上限", 160, 20, 1000),
        (right, "negative-ratio", "负例与正例推理长度比上限（%）", 25, 5, 50),
    ):
        key = f"workflow-preference-{name}:{workspace}"
        st.session_state[key] = int(_value(workspace, name, default))
        column.number_input(label, min_value=minimum, max_value=maximum, step=5, key=key,
            on_change=save_field, args=(workspace, key))
    st.caption("长度限制只用于合成负例。真实失败稿不截短，双方仍独立评分并满足原有分差；提示词可在本节点修改。")
