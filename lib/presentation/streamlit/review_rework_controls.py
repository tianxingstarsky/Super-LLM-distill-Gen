"""Save rejected samples and continue to their persistent rework collection."""
from __future__ import annotations

import streamlit as st
from filelock import Timeout

from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.review_navigation import remember_task_return


def _open_collection(collection, run_id, target, widgets):
    workspace = widgets.session_state.get("ws", "default")
    session_id = collection["session_id"]
    remember_task_return("human:" + session_id, run_id, target, widgets=widgets)
    widgets.session_state[f"human-selected:{workspace}"] = session_id
    context = f"human:{workspace}:{session_id}"
    counts = collection.get("issue_counts", {})
    status = next((name for name in ("pending", "needs_revision") if counts.get(name, 0)), "all")
    widgets.session_state[f"human-result-view-pending:{context}"] = "issues"
    widgets.session_state[f"rework-status:{context}"] = status
    widgets.session_state[f"rework-offset:{context}:{status}"] = 0
    widgets.session_state["nav"] = "人工问答增强"
    widgets.query_params["human"] = session_id
    widgets.rerun()


def render_review_rework_controls(application, run_id: str, target: str, rejected_count: int, *, widgets=st):
    if application is None:
        return
    prefix = f"review-rework:{run_id}:{target}"
    widgets.caption("保存退回结果后可直接进入返工，也可留待稍后处理。模型不会自动启动。")
    instruction = widgets.text_input("共同修正要求（可选）", key=prefix + ":instruction", max_chars=3999,
        help="保留每条已有审核意见；这里填写需要追加给所有退回结果的要求。保存不会调用模型。")
    save_and_open = widgets.button("保存并打开返工", key=prefix + ":save-open", width="stretch",
                                  type="primary", disabled=rejected_count <= 0)
    save_only = widgets.button("仅保存退回结果", key=prefix + ":save", width="stretch",
                               disabled=rejected_count <= 0)
    if save_and_open or save_only:
        try:
            with widgets.spinner("正在保存退回结果…"):
                result = application.save_rejected(run_id, target, instruction=instruction)
            if save_and_open and result["saved_count"] > 0:
                _open_collection(result, run_id, target, widgets)
                return
            if not result["saved_count"]:
                widgets.info("当前没有可存入的退回结果，请刷新审核列表。")
        except (OSError, ValueError, Timeout, KeyError):
            widgets.error("退回结果未能全部保存。已保存的问题仍保留，请检查原任务与本机存储后重试。")
    try:
        collection = application.existing_collection(run_id, target)
    except (OSError, ValueError, Timeout, KeyError):
        widgets.error("返工集合暂时无法打开，请检查原任务与本机存储后重试。")
        return
    if collection:
        saved = collection["saved_count"]
        widgets.caption(UntranslatedText(f"{saved:,} results saved for rework. Opening the collection does not start a model."
            if widgets.session_state.get("ui_language", "zh") == "en" else
            f"已存入返工集合 {saved:,} 条。打开集合不会启动模型。"))
        if widgets.button("打开已存返工", key=prefix + ":open", width="stretch"):
            _open_collection(collection, run_id, target, widgets)
