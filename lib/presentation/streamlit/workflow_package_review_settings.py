"""Optional JEV scoring configured on its own workflow node."""
from __future__ import annotations

import streamlit as st

from lib.domain.workflow_package_review import (
    DEFAULT_PACKAGE_REVIEW_LIMIT, DEFAULT_PACKAGE_REVIEW_PERCENT,
    MAX_PACKAGE_REVIEW_SAMPLES,
)
from lib.presentation.streamlit.i18n import translate_label


def _field(workspace, key, default):
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    return draft.get(key, st.session_state.get(key, default))


def package_review_snapshot(workspace: str) -> dict:
    enabled = _field(workspace, f"workflow-package-review-enabled:{workspace}", False)
    if not enabled:
        return {"enabled": False, "node": "jev"}
    return {
        "enabled": enabled,
        "node": "jev",
        "mode": _field(workspace, f"workflow-package-review-mode:{workspace}", "sample"),
        "sample_percent": _field(workspace, f"workflow-package-review-percent:{workspace}", DEFAULT_PACKAGE_REVIEW_PERCENT),
        "max_samples_per_target": _field(workspace, f"workflow-package-review-limit:{workspace}", DEFAULT_PACKAGE_REVIEW_LIMIT),
        "escalate_failure_percent": _field(workspace, f"workflow-package-review-escalation:{workspace}", 0.0),
    }


def render_package_review_toggle(workspace: str, *, save_field) -> None:
    # Existing local drafts stored this prompt on packaging. Move a copy to
    # the new node once; a user's later JEV edits always take precedence.
    draft = st.session_state.get(f"workflow-form-draft:{workspace}", {})
    old_prompt = f"workflow-node-prompt:{workspace}:package:workflow.package_review"
    new_prompt = f"workflow-node-prompt:{workspace}:jev:workflow.package_review"
    if new_prompt not in draft and new_prompt not in st.session_state:
        saved_prompt = draft.get(old_prompt, st.session_state.get(old_prompt))
        if saved_prompt is not None:
            st.session_state[new_prompt] = saved_prompt
            save_field(workspace, new_prompt)
    enabled_key = f"workflow-package-review-enabled:{workspace}"
    st.session_state[enabled_key] = _field(workspace, enabled_key, False)
    st.toggle("加入 JEV 评分", key=enabled_key,
                        on_change=save_field, args=(workspace, enabled_key),
                        help="在输出前加入可选的模型评分节点，可抽检或全量评审。关闭不影响生成节点的基础自检与打包规则。")


def render_package_review_settings(workspace: str, *, save_field) -> None:
    if not package_review_snapshot(workspace)["enabled"]:
        return
    language = st.session_state.get("ui_language", "zh")
    for name, default in (("mode", "sample"),
                          ("percent", DEFAULT_PACKAGE_REVIEW_PERCENT),
                          ("limit", DEFAULT_PACKAGE_REVIEW_LIMIT)):
        key = f"workflow-package-review-{name}:{workspace}"
        st.session_state[key] = _field(workspace, key, default)
    mode_key = f"workflow-package-review-mode:{workspace}"
    mode = st.segmented_control("评审范围", ("sample", "all"), key=mode_key,
                                selection_mode="single", required=True,
                                format_func=lambda value: translate_label("抽检" if value == "sample" else "全量评审", language),
                                on_change=save_field, args=(workspace, mode_key))
    if mode == "sample":
        percent_key = f"workflow-package-review-percent:{workspace}"
        limit_key = f"workflow-package-review-limit:{workspace}"
        percent, limit = st.columns(2, gap="small")
        with percent:
            st.number_input("抽检比例（%）", min_value=0.01, max_value=100.0, step=0.5,
                            format="%.2f", key=percent_key,
                            on_change=save_field, args=(workspace, percent_key))
        with limit:
            st.number_input("每类抽检上限", min_value=1, max_value=MAX_PACKAGE_REVIEW_SAMPLES,
                            step=100, key=limit_key,
                            on_change=save_field, args=(workspace, limit_key))
        st.caption("按比例抽取，且不超过每类上限。未抽中样本沿用上游质检结果；报告单独列出覆盖率。")
        escalation_key = f"workflow-package-review-escalation:{workspace}"
        st.session_state[escalation_key] = _field(workspace, escalation_key, 0.0)
        st.number_input("抽检失败升级阈值（%）", min_value=0.0, max_value=100.0,
                        step=1.0, format="%.1f", key=escalation_key,
                        on_change=save_field, args=(workspace, escalation_key),
                        help="自动补齐模式生效。0 表示任一失败即对本轮该类数据升级全量评审；其他值按抽检失败比例触发，仍受预算限制。")
        st.caption("抽检发现问题后可扩大本轮检查，保留已完成评审，不重复调用。")
    else:
        st.caption("逐条评审所有规则检查合格的样本；请求数随数据量增加，请设置预算。")
    st.caption("评审支持并发、预算限制和断点续跑；点击运行中的节点可查看模型流式输出。")
