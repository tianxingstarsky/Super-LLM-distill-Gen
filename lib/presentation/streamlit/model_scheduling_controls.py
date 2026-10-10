"""Expose shared model admission limits where the model is selected."""
from __future__ import annotations

import math

import streamlit as st
from filelock import Timeout

from lib.presentation.streamlit.i18n import UntranslatedText


def _save_scheduling(application, backend, model, prefix):
    try:
        application.save_model_scheduling(
            backend, model,
            max_concurrency=st.session_state[prefix + ":limit"],
            request_queue_timeout_seconds=st.session_state[prefix + ":wait"],
        )
        st.session_state.pop(prefix + ":error", None)
    except (OSError, ValueError, Timeout):
        st.session_state[prefix + ":error"] = True


def scheduling_caption(state, language):
    active, queued, limit = state["active"], state["queued"], state["max_concurrency"]
    if language == "en":
        text = f"Shared pool · {active} / {limit} active · {queued} queued"
    else:
        text = f"共享请求池 · 占用 {active} / {limit} · 排队 {queued}"
    cooldown = math.ceil(state.get("cooldown_seconds", 0))
    if cooldown:
        text += f" · Retry in {cooldown}s" if language == "en" else f" · 限流等待 {cooldown} 秒"
    return UntranslatedText(text)


def render_model_scheduling(application, backend, model, *, prefix):
    if application is None or not callable(getattr(application, "get_model_scheduling", None)):
        return
    prefix += ":scheduling:" + backend + ":" + model
    try:
        state = application.get_model_scheduling(backend, model)
    except (OSError, ValueError, Timeout):
        st.warning("模型调度状态暂不可读，请检查本机存储后重试。")
        return
    # Refresh every role from the authority; a stale sibling widget must not
    # restore its old limit when another node changes this shared resource.
    st.session_state[prefix + ":limit"] = state["max_concurrency"]
    st.session_state[prefix + ":wait"] = state["request_queue_timeout_seconds"]
    limit_column, options_column = st.columns([3, 1], gap="small", vertical_alignment="bottom")
    with limit_column:
        st.number_input(
            "此模型最大并发请求", min_value=1, max_value=256,
            key=prefix + ":limit", on_change=_save_scheduling,
            args=(application, backend, model, prefix),
            help="相同服务、凭据与模型共享上限，涵盖全部节点与任务。流水线并发另行控制每个任务的处理量。降低上限不会截断正在输出的请求。",
        )
    with options_column:
        with st.popover("排队设置", width="stretch"):
            st.number_input(
                "最长排队等待（秒）", min_value=1, max_value=3600,
                key=prefix + ":wait", on_change=_save_scheduling,
                args=(application, backend, model, prefix),
                help="超时会保留断点并报告原因；排队和退避期间可响应停止。此值不限制已经开始的输出时长。",
            )
    st.caption(scheduling_caption(state, st.session_state.get("ui_language", "zh")))
    if st.session_state.get(prefix + ":error"):
        st.error("并发设置未能保存，已恢复当前生效值。请检查本机存储后重试。")


def render_run_scheduling(application, bindings):
    """Live operational state belongs outside a run's immutable recipe."""
    if application is None:
        return
    seen = set()
    for binding in bindings.values():
        backend, model = binding.get("backend"), binding.get("model")
        if not backend or not model or (backend, model) in seen:
            continue
        seen.add((backend, model))
        try:
            state = application.get_model_scheduling(backend, model)
        except (OSError, ValueError, Timeout):
            st.caption("模型调度状态暂不可读，请检查本机存储后重试。")
            continue
        st.caption(UntranslatedText(model))
        st.caption(scheduling_caption(state, st.session_state.get("ui_language", "zh")))
