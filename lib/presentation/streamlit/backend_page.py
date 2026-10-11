"""Model service credentials and recorded usage for the console."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import html

import streamlit as st

from lib.application.backend_service import BackendApplication
from lib.model_protocols import API_FORMATS
from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.backend_style import CSS
from lib.presentation.streamlit.shared import page_header


_API_FORMAT_LABELS = {"chat": "Chat Completions", "responses": "OpenAI Responses",
                      "anthropic": "Anthropic Messages"}
_DEFAULT_URLS = {"chat": "http://127.0.0.1:11434/v1", "responses": "https://api.openai.com/v1",
                 "anthropic": "https://api.anthropic.com"}
_DEFAULT_NAMES = {"chat": "local_gpu", "responses": "openai_responses", "anthropic": "anthropic"}


def _safe(value: object) -> str:
    return html.escape(str(value), quote=True)


def _credential_ready(row: dict) -> bool:
    # list_backends() already masks keys. The UI only presents the presence
    # state, so even a masked suffix is never rendered into page markup.
    return str((row.get("api_key") or {}).get("status", "缺失")) != "缺失"


def _summary(info: dict) -> None:
    rows = info.get("backends") or []
    configured = sum(_credential_ready(row) for row in rows)
    spent = float(info.get("spent") or 0)
    cards = (
        ("已登记端点", str(len(rows)), "可供工作流选择的模型服务", "blue"),
        ("密钥已配置", str(configured), "仅表示凭据存在，连接需单独测试", "green"),
        ("模型选择位置", "工作流节点", "点击节点分别选择生成与评审模型", "purple"),
        ("历史费用记录", f"${spent:.2f}", "仅保留旧版本记录", "amber"),
    )
    st.html('<div class="df-model-summary">' + ''.join(
        '<div data-tone="' + tone + '"><small>' + _safe(label) + '</small><strong>'
        + _safe(value) + '</strong><em>' + _safe(note) + '</em></div>'
        for label, value, note, tone in cards
    ) + '</div>')
    st.html('<div class="df-model-context"><b>服务地址与凭据</b>'
            '<i>›</i><span>服务端点提供模型</span><i>›</i><span>点击工作流节点选择模型</span>'
            '<i>›</i><span>用量写入审计</span></div>')


def _panel_heading(title: str, description: str, icon: str, aside: str = "") -> None:
    st.html('<div class="df-model-panel-title"><b>' + _safe(icon) + '</b><div><strong>'
            + _safe(title) + '</strong><small>' + _safe(description) + '</small></div>'
            + ('<span>' + _safe(aside) + '</span>' if aside else '') + '</div>')


def _endpoints(rows: list[dict]) -> None:
    _panel_heading("服务端点", "查看服务地址、模型列表与凭据状态；连接可用性以实际测试为准。", "⬡", f"{len(rows)} 个端点")
    if not rows:
        st.html('<div class="df-model-empty">尚无服务端点。展开下方配置区添加第一个模型服务。</div>')
        return
    cards = []
    for row in rows:
        api_format = row.get("api_format", "chat")
        format_label = _API_FORMAT_LABELS.get(api_format, str(api_format))
        models = row.get("models") or []
        model_tags = ''.join('<span data-user-content>' + _safe(name) + '</span>' for name in models[:3])
        if len(models) > 3:
            model_tags += '<span>+' + str(len(models) - 3) + '</span>'
        if not model_tags:
            model_tags = '<i>暂未设置模型</i>'
        role_detail = role_text = "可在工作流节点中选用"
        ready = _credential_ready(row)
        cards.append(
            '<div class="df-model-endpoint" data-default="' + str(bool(row.get("is_default"))).lower() + '">'
            '<div class="df-model-endpoint-head"><span class="df-model-endpoint-icon">⬡</span><strong data-user-content>'
            + _safe(row.get("name", "")) + '</strong>'
            + ('<em>默认端点</em>' if row.get("is_default") else '') + '</div>'
            '<div class="df-model-endpoint-models">' + model_tags + '</div>'
            '<div class="df-model-endpoint-address" data-user-content title="' + _safe(row.get("base_url", "")) + '">'
            + _safe(format_label) + ' · ' + _safe(row.get("base_url", "")) + '</div>'
            '<div class="df-model-endpoint-foot"><span title="' + _safe(role_detail) + '">'
            + _safe(role_text) + '</span>'
            '<b data-ready="' + str(ready).lower() + '">' + ("密钥已配置" if ready else "密钥未配置") + '</b></div></div>'
        )
    st.html('<div class="df-model-endpoints">' + ''.join(cards) + '</div>')


def _endpoint_form(application: BackendApplication, *, inline: bool = False) -> None:
    _panel_heading("配置端点", "保存到本地覆盖文件，自动保留上一个版本。", "＋")
    with (nullcontext() if inline else st.expander("新增或覆盖端点", expanded=False)):
        st.caption("推荐使用环境变量存放密钥。环境变量模式只保存变量名；手动密钥模式会写入本地配置。")
        api_format = st.selectbox("API 协议", API_FORMATS, key="backend-add-api-format",
                                  format_func=lambda value: _API_FORMAT_LABELS[value],
                                  help="选择端点实际支持的调用协议。")
        with st.form("backend-add"):
            left, right = st.columns(2)
            name = left.text_input("后端名", _DEFAULT_NAMES[api_format],
                                   key=f"backend-add-name:{api_format}")
            base_url = right.text_input("服务 API 地址", _DEFAULT_URLS[api_format],
                                        key=f"backend-add-base-url:{api_format}")
            models = st.text_input("模型名（逗号分隔）",
                                   "qwen2.5:7b-instruct" if api_format == "chat" else "",
                                   key=f"backend-add-models:{api_format}",
                                   placeholder="填写所选服务提供的模型名")
            mode = st.radio("密钥来源", ["环境变量（推荐）", "写入本地配置"], horizontal=True)
            secret = st.text_input("密钥（环境变量名 或 密钥值）", type="password")
            overwrite = st.checkbox("覆盖同名后端")
            submit = st.form_submit_button("保存端点", type="primary")
        if submit:
            try:
                # Updating a connection preserves prior accounting metadata.
                # New services do not need local prices to become usable.
                prices_obj = deepcopy((application.merged_backends().get(name.strip()) or {}).get("prices") or None)
                model_names = [part.strip() for part in models.split(",") if part.strip()]
                default_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
                if mode.startswith("环境变量"):
                    application.save_endpoint(name, base_url, model_names, api_key_env=secret or default_env,
                                     prices=prices_obj, explicit_replace=overwrite, api_format=api_format)
                else:
                    application.save_endpoint(name, base_url, model_names, api_key=secret,
                                     prices=prices_obj, explicit_replace=overwrite, api_format=api_format)
                st.toast(f"已保存 {name}，原文件已备份")
                st.rerun()
            except (ValueError, FileExistsError) as error:
                st.error(str(error))


def _connection_probe(application: BackendApplication, names: list[str], default: str) -> None:
    _panel_heading("连接检查", "读取所选服务的模型列表，不发起生成请求。", "↗")
    st.caption("密钥状态只表示本机配置是否存在。这里的主动检查才会访问对应端点。")
    if not names:
        st.info("添加端点后即可进行连接检查。")
        return
    target = st.selectbox("选择后端", names, index=names.index(default) if default in names else 0,
                          key="backend-test-name", format_func=UntranslatedText)
    if st.button("测试连接", type="primary", width="stretch"):
        try:
            result = application.test_backend(target)
            models = result.get("models") or []
            st.success(f"{target} 可达，返回 {len(models)} 个模型：{', '.join(map(str, models[:12]))}")
        except Exception as error:  # noqa: BLE001 - remote SDK errors may include credentials
            st.error(f"连接失败（{type(error).__name__}）。请检查地址、密钥与服务状态。")


def _usage(info: dict) -> None:
    """Label the retained legacy amount; current token usage belongs to tasks."""
    spent = float(info.get("spent") or 0)
    with st.container(border=True, key="model-recorded-usage"):
        _panel_heading("历史费用记录", "仅保留旧版本记录", "◷")
        st.html('<div class="df-model-budget"><div class="df-model-budget-top"><div><small>历史费用记录</small>'
                '<strong>' + _safe(f"${spent:.4f}") + '</strong><em>仅保留旧版本记录</em></div></div></div>')
        st.caption("仅保留旧版本记录；当前任务的调用次数和 token 用量见任务管理。")


def render_backend_page(application: BackendApplication, *, embedded: bool = False) -> None:
    """Render shared connections and usage inside settings."""
    if not embedded:
        page_header("模型服务", "登记服务地址与凭据；具体模型在工作流节点中选择。", "MODEL CONNECTIONS")
    st.html(CSS)
    info = application.list_backends()
    rows = info.get("backends") or []
    names = [row["name"] for row in rows]
    if not embedded:
        _summary(info)
    endpoint_tab, usage_tab = st.tabs(["端点与连接", "用量记录"])
    with endpoint_tab:
        with st.container(border=True):
            _endpoints(rows)
        left, right = st.columns([1.35, 1], gap="medium")
        with left, st.container(border=True):
            _endpoint_form(application, inline=embedded)
        with right, st.container(border=True):
            _connection_probe(application, names, info.get("default_backend", ""))
    with usage_tab:
        _usage(info)
