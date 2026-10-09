"""Discover and verify model capabilities beside the selected workflow model."""
from __future__ import annotations

from copy import deepcopy
import html

import streamlit as st

from lib.presentation.streamlit.i18n import UntranslatedText, translate
from lib.domain.workflow_scale import (DEFAULT_CONTEXT_WINDOW_TOKENS,
                                       DEFAULT_MAX_OUTPUT_TOKENS,
                                       MAX_CONTEXT_WINDOW_TOKENS)


FEATURE_LABELS = {"text": "文本", "vision": "图片", "pdf": "原生 PDF", "tools": "工具调用"}
SOURCE_LABELS = {"manual": "手动设置", "provider": "服务返回", "service": "服务返回", "catalog": "公开元数据",
                 "models_dev": "公开元数据", "models.dev": "公开元数据", "probe": "调用测试", "test": "调用测试",
                 "unknown": "未知"}
STATUS_LABELS = {"pass": "通过", "passed": "通过", "supported": "通过",
                 "fail": "失败", "failed": "失败", "error": "失败",
                 "unsupported": "不支持", "unknown": "未确认", "skipped": "已跳过"}
REASON_LABELS = {
    "probe_reply_mismatch": "结果未通过内容核验", "probe_incomplete": "测试输出不完整",
    "feature_rejected": "服务拒绝该输入能力", "authentication_failed": "凭据校验失败",
    "permission_denied": "服务权限不足", "rate_limited": "服务请求限流",
    "request_timeout": "调用超时", "connection_failed": "服务连接失败",
    "provider_unavailable": "模型服务暂不可用", "model_not_found": "服务未找到该模型",
    "request_rejected": "服务拒绝测试请求", "unexpected_probe_error": "模型测试发生异常",
    "sdk_unavailable": "缺少调用依赖", "invalid_probe_config": "测试连接配置无效",
    "not_requested": "本次未选择", "text_connection_failed": "文本连接失败，已跳过后续测试",
    "earlier_connection_failed": "前序调用失败，已跳过后续测试",
}


def model_info(application, backend: str, model: str, endpoint: dict | None = None) -> dict:
    """Prefer the application's merged evidence; inventory remains a safe fallback."""
    getter = getattr(application, "get_model_info", None)
    if getter is not None:
        try:
            return getter(backend, model) or {}
        except (OSError, ValueError):
            pass
    return deepcopy(((endpoint or {}).get("model_info") or {}).get(model) or {})


def suggested_tokens(info: dict) -> tuple[int, int]:
    context = info.get("context_window_tokens")
    output = info.get("max_output_tokens")
    context = (min(context, MAX_CONTEXT_WINDOW_TOKENS) if type(context) is int and context > 1
               else DEFAULT_CONTEXT_WINDOW_TOKENS)
    output = (min(output, DEFAULT_MAX_OUTPUT_TOKENS) if type(output) is int and output > 0
              else DEFAULT_MAX_OUTPUT_TOKENS)
    return context, min(output, context - 1)


def discovered_choices(endpoint: dict) -> list[str]:
    return list(dict.fromkeys(model for model in
                             [*(endpoint.get("models") or []), *(endpoint.get("discovered_models") or [])]
                             if isinstance(model, str) and model.strip()))


_DISCOVERY_MESSAGES = {
    "success": "模型列表已更新。",
    "empty": "服务未返回模型列表，仍可手动输入模型名。",
    "truncated": "模型列表过长或响应超时，仅显示已获取的模型。",
    "failed": "未能获取模型列表，请检查地址、协议和凭据。仍可手动输入模型名。",
}


def _discover_models_on_click(application, backend: str, key: str) -> None:
    """Refresh persisted inventory before the next complete widget render."""
    status = "failed"
    truncated = False
    try:
        with st.spinner("正在获取模型列表…"):
            result = application.discover_models(backend)
    except (OSError, ValueError, RuntimeError):
        pass
    else:
        if result.get("ok", True) and isinstance(result.get("models"), list):
            status = "success" if result["models"] else "empty"
            truncated = bool(result.get("truncated"))
    st.session_state[key + ":feedback"] = {"backend": backend, "status": status, "truncated": truncated}
    if status != "failed":
        st.toast(_DISCOVERY_MESSAGES["truncated" if truncated else status])


def render_model_discovery(application, backend: str, endpoint: dict, *, key: str) -> dict:
    if application is None or not hasattr(application, "discover_models"):
        return endpoint
    st.button("获取模型", key=key, width="stretch",
              on_click=_discover_models_on_click, args=(application, backend, key),
              help="从当前服务获取可用模型。保留已选模型与当前节点参数。")
    feedback = st.session_state.pop(key + ":feedback", {})
    if feedback.get("backend") == backend:
        status = feedback["status"]
        if status == "success":
            st.success(_DISCOVERY_MESSAGES[status])
        elif status == "empty":
            st.info(_DISCOVERY_MESSAGES[status])
        else:
            st.warning(_DISCOVERY_MESSAGES["failed"])
        if feedback.get("truncated"):
            st.warning(_DISCOVERY_MESSAGES["truncated"])
    return endpoint


def _source(info: dict, field: str) -> str:
    source = (info.get("sources") or {}).get(field, "unknown")
    if isinstance(source, dict):
        source = source.get("source", "unknown")
    return SOURCE_LABELS.get(str(source), "未知")


def _apply_suggestion(prefix: str, backend: str, model: str, info: dict, save_selection, save_args) -> None:
    context, output = suggested_tokens(info)
    st.session_state[prefix + ":context:" + backend + ":" + model] = context
    st.session_state[prefix + ":output:" + backend + ":" + model] = output
    save_selection(*save_args)


def _render_capability_summary(info: dict) -> None:
    """Keep capabilities visible; provenance remains available on hover."""
    rows = []
    language = st.session_state.get("ui_language", "zh")
    for field, label in (("vision", "图片"), ("pdf", "原生 PDF"), ("tools", "工具调用")):
        value = info.get(field)
        state = "支持" if value is True else "不支持" if value is False else "未知"
        source = _source(info, field)
        summary = translate(label, language) + " · " + translate(state, language)
        provenance = ' title="' + html.escape(translate(source, language), quote=True) + '"' if source != "未知" else ""
        color = "#127c63" if value is True else "#b65645" if value is False else "#64748b"
        rows.append('<span style="display:inline-block;padding:3px 6px;margin:0 4px 4px 0;'
                    'border:1px solid #dfe8f3;border-radius:6px;background:#f7faff;font-size:11px;color:'
                    + color + '"' + provenance + '>' + html.escape(summary) + '</span>')
    st.html('<div style="line-height:1.5;padding-top:3px">' + ''.join(rows) + '</div>')


def _render_capacity_settings(application, backend, model, info, *, prefix, save_selection, save_args) -> None:
    capacities = []
    for field, label in (("context_window_tokens", "上下文容量"), ("max_output_tokens", "最大输出容量")):
        value = info.get(field)
        if type(value) is int and value > 0:
            capacities.append(f"{label} {value:,} · {_source(info, field)}")
    if capacities:
        st.caption("；".join(capacities))
        if save_selection and st.button("采用建议上限", key=prefix + ":apply-limits",
                                         on_click=_apply_suggestion,
                                         args=(prefix, backend, model, deepcopy(info), save_selection, save_args),
                                         help="仅更新当前节点。已有草稿不会自动覆盖。"):
            pass
    else:
        st.caption("服务未提供容量信息，可调整节点上限。短请求测试不能证明完整上下文容量。")
    if not hasattr(application, "save_model_capabilities"):
        return
    st.caption("手动声明与调用测试分别保存；恢复未知会清除当前手动覆盖。")
    manual = info.get("manual") or {}
    columns = st.columns(3, gap="small")
    values = {}
    for column, field in zip(columns, ("vision", "pdf", "tools")):
        with column:
            value = manual.get(field)
            if isinstance(value, dict):
                value = value.get("value")
            values[field] = st.selectbox(FEATURE_LABELS[field] + "能力", (None, True, False),
                                          index=1 if value is True else 2 if value is False else 0,
                                          format_func=lambda value: "支持" if value is True else "不支持" if value is False else "未知",
                                          key=prefix + ":manual:" + backend + ":" + model + ":" + field)
    capacity_columns = st.columns(2, gap="small")
    for column, field, label in ((capacity_columns[0], "context_window_tokens", "模型上下文容量"),
                                 (capacity_columns[1], "max_output_tokens", "模型最大输出容量")):
        with column:
            value = manual.get(field)
            if isinstance(value, dict):
                value = value.get("value")
            values[field] = st.number_input(label, min_value=1, max_value=MAX_CONTEXT_WINDOW_TOKENS,
                                            value=value if type(value) is int and value > 0 else None,
                                            step=1024,
                                            key=prefix + ":manual:" + backend + ":" + model + ":" + field)
    if st.button("保存能力设置", key=prefix + ":save-capabilities"):
        try:
            application.save_model_capabilities(backend, model, **values)
        except (OSError, ValueError):
            st.error("能力设置未保存，请检查数值和本机配置后重试。")
        else:
            st.rerun()


def _render_call_tests(application, backend, model, info, *, prefix) -> None:
    if info.get("vision") is True and _source(info, "vision") == "公开元数据":
        st.caption("公开元数据仅作参考，图片输入需调用测试或手动确认。")
    if application is None or not hasattr(application, "test_model"):
        return
    test_columns = st.columns([2, 1], gap="small", vertical_alignment="bottom")
    with test_columns[0]:
        features = st.multiselect("测试项目", list(FEATURE_LABELS), default=list(FEATURE_LABELS),
                                  format_func=FEATURE_LABELS.get, key=prefix + ":test-features")
    with test_columns[1]:
        test = st.button("测试模型调用", key=prefix + ":test", disabled=not features, width="stretch")
    if test:
        try:
            with st.spinner("正在发送少量测试内容，验证所选模型能力…"):
                result = application.test_model(backend, model, features=features)
        except (OSError, ValueError, RuntimeError) as error:
            if str(error) == "model_budget_prices_required":
                st.warning("请先为此连接填写单价；硬预算不会调用价格未知的模型。")
            elif type(error).__name__ == "BudgetExceeded":
                if str(error) == "Budget exhausted; no request sent":
                    st.warning("预算不足，未发送模型测试。请检查剩余额度。")
                else:
                    st.warning("测试用量超过预留预算，已记录费用。请检查计费记录后再调用。")
            elif str(error) == "model_connection_changed":
                st.warning("测试期间连接已变更，结果未用于当前模型。请重新测试。")
            else:
                st.warning("模型测试未完成，请检查服务连接后重试。")
        else:
            st.rerun()
    # These records have already been matched to the current endpoint by the
    # application. A stale session result must never survive a connection edit.
    evidence = info.get("probes") or {}
    tests = (evidence.get("last_tests") or evidence.get("tests") or evidence) if isinstance(evidence, dict) else {}
    if isinstance(tests, dict):
        summaries = []
        reasons = []
        for feature, outcome in tests.items():
            if feature in FEATURE_LABELS and isinstance(outcome, dict):
                summaries.append(FEATURE_LABELS[feature] + " · " + STATUS_LABELS.get(outcome.get("status"), "未确认"))
                reason = REASON_LABELS.get(outcome.get("reason_code"))
                if reason and outcome.get("status") not in {"passed", "pass"}:
                    reasons.append(FEATURE_LABELS[feature] + "：" + reason)
        if summaries:
            st.caption("调用测试：" + "；".join(summaries))
            for reason in reasons:
                st.caption(reason)
            tested_at = next((outcome.get("tested_at") for outcome in tests.values()
                              if isinstance(outcome, dict) and outcome.get("tested_at")), evidence.get("tested_at"))
            if tested_at:
                st.caption("测试时间：" + str(tested_at))
    st.caption("测试会发送少量测试内容，并可能产生调用费用。公开信息与实测结果分别显示；短请求不验证最大容量。")


def render_model_capabilities(application, backend: str, model: str | None, info: dict, *, prefix: str,
                              endpoint=None, save_selection=None, save_args=()) -> None:
    """Keep common choices outside the explicitly opened diagnostic surface."""
    summary, diagnostics = st.columns([2.5, 1], gap="small", vertical_alignment="center")
    with summary:
        if model:
            _render_capability_summary(info)
        else:
            st.caption("可获取列表，或直接输入模型名。")
    with diagnostics:
        with st.popover("模型检测", width="stretch",
                         help="获取模型列表，测试文本、图片、PDF 与工具调用，或调整能力声明。"):
            st.caption(UntranslatedText(backend + (" · " + model if model else "")))
            if endpoint is not None:
                render_model_discovery(application, backend, endpoint, key=prefix + ":discover")
            if not model:
                st.caption("选择模型后可测试调用能力。")
                return
            calls, settings = st.tabs(["调用检测", "能力设置"])
            with calls:
                _render_call_tests(application, backend, model, info, prefix=prefix)
            with settings:
                _render_capacity_settings(application, backend, model, info, prefix=prefix,
                                           save_selection=save_selection, save_args=save_args)
