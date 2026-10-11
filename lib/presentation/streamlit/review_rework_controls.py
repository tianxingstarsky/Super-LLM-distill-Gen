"""Two compact actions attach existing rejection queues to batch rework."""
from __future__ import annotations

import streamlit as st
from filelock import Timeout

from lib.presentation.streamlit.i18n import UntranslatedText


def _open_collection(application, run_id, widgets):
    collection = application.collection(run_id)
    workspace = widgets.session_state.get("ws", "default")
    session_id = collection["session_id"]
    widgets.session_state[f"human-selected:{workspace}"] = session_id
    widgets.session_state[f"human-result-view-pending:human:{workspace}:{session_id}"] = "issues"
    widgets.session_state["nav"] = "人工问答增强"
    widgets.query_params["human"] = session_id
    widgets.rerun()


def render_review_rework_controls(application, run_id: str, target: str, rejected_count: int, *, widgets=st):
    if application is None:
        return
    prefix = f"review-rework:{run_id}:{target}"
    widgets.caption("退回结果可先存入返工集合，稍后集中修正。")
    instruction = widgets.text_input("共同修正要求（可选）", key=prefix + ":instruction", max_chars=3999,
        help="保留每条已有审核意见；这里填写需要追加给所有退回结果的要求。保存不会调用模型。")
    if widgets.button("把退回结果存入返工集合", key=prefix + ":save", width="stretch",
                      disabled=rejected_count <= 0):
        try:
            with widgets.spinner("正在保存退回结果…"):
                result = application.save_rejected(run_id, target, instruction=instruction)
            widgets.session_state[prefix + ":saved"] = result["saved_count"]
        except (OSError, ValueError, Timeout, KeyError):
            widgets.error("退回结果未能全部保存。已保存的问题仍保留，请检查原任务与本机存储后重试。")
    if saved := widgets.session_state.get(prefix + ":saved"):
        widgets.caption(UntranslatedText(f"{saved:,} results saved for rework. No model has been started."
            if widgets.session_state.get("ui_language", "zh") == "en" else
            f"已存入返工集合 {saved:,} 条。模型尚未启动。"))
    if widgets.button("打开返工集合", key=prefix + ":open", width="stretch"):
        try:
            _open_collection(application, run_id, widgets)
        except (OSError, ValueError, Timeout, KeyError):
            widgets.error("返工集合暂时无法打开，请检查原任务与本机存储后重试。")
