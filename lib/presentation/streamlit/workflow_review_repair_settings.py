"""Keep scoring and repair controls together on their workflow node."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.i18n import translate_label


def _field(workspace, name, default):
    key = f"workflow-review-repair-{name}:{workspace}"
    return st.session_state.get(key, st.session_state.get(
        f"workflow-form-draft:{workspace}", {}).get(key, default))


def review_repair_snapshot(workspace: str) -> dict:
    return {"mode": _field(workspace, "mode", "auto"),
            "max_rounds": int(_field(workspace, "rounds", 2)),
            "score_threshold": float(_field(workspace, "threshold", 80)) / 100}


def render_review_repair_settings(workspace: str, package_review: dict, *, save_field) -> dict:
    language = st.session_state.get("ui_language", "zh")
    for name, default in (("mode", "auto"), ("rounds", 2), ("threshold", 80)):
        key = f"workflow-review-repair-{name}:{workspace}"
        if key not in st.session_state:
            st.session_state[key] = _field(workspace, name, default)
    mode_key = f"workflow-review-repair-mode:{workspace}"
    mode = st.segmented_control("评分与修正方式", ("auto", "human"), required=True,
        key=mode_key, format_func=lambda value: translate_label(
            "自动评分与修正" if value == "auto" else "人工评分与修正", language),
        on_change=save_field, args=(workspace, mode_key))
    threshold, rounds = st.columns(2, gap="small")
    threshold_key = f"workflow-review-repair-threshold:{workspace}"
    rounds_key = f"workflow-review-repair-rounds:{workspace}"
    with threshold:
        st.number_input("通过分数", min_value=0, max_value=100, step=5, key=threshold_key,
            on_change=save_field, args=(workspace, threshold_key))
    with rounds:
        st.number_input("自动修正次数上限", min_value=0, max_value=5, step=1,
            key=rounds_key, disabled=mode == "human",
            on_change=save_field, args=(workspace, rounds_key),
            help="低于通过分数时，修正答案后重新评分。达到上限仍未通过的样本保留在待处理结果中。0 表示只评分。")
    if mode == "human":
        st.caption("人工在同一节点查看结果、评分并编辑内容；提交后保留独立版本，不调用模型改写。")
    elif package_review.get("enabled"):
        st.caption("本节点配置两个角色：JEV 模型负责评分，修正模型负责修改答案。修正后回到本节点再次评分。")
    else:
        st.caption("本节点的同一个模型负责评分和修正。修正分支修改当前候选，再返回评分；不会回到 SFT 生成节点。")
    return review_repair_snapshot(workspace)
