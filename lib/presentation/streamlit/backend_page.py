"""Model endpoint, role and budget administration for the Streamlit console."""
from __future__ import annotations

import html
import json

import streamlit as st

from lib import backend_manager as bm
from lib.presentation.streamlit.backend_style import CSS
from lib.presentation.streamlit.shared import page_header


ROLE_LABELS = {
    "generation": "数据生成", "judge": "质量评审", "jev": "JEV 独立评分",
    "vision": "视觉解析", "refine": "数据改写", "simulate": "模拟环境",
    "translation": "翻译",
}


def _safe(value: object) -> str:
    return html.escape(str(value), quote=True)


def _credential_ready(row: dict) -> bool:
    # list_backends() already masks keys. The UI only presents the presence
    # state, so even a masked suffix is never rendered into page markup.
    return str((row.get("api_key") or {}).get("status", "缺失")) != "缺失"


def _summary(info: dict) -> None:
    rows = info.get("backends") or []
    roles = info.get("roles") or {}
    assigned = sum(bool((roles.get(role) or {}).get("backend") and (roles.get(role) or {}).get("model"))
                   for role in ROLE_LABELS)
    configured = sum(_credential_ready(row) for row in rows)
    default = next((row["name"] for row in rows if row.get("is_default")), "未设置")
    spent = float(info.get("spent") or 0)
    limit = float((info.get("budget") or {}).get("max_total_usd") or 0)
    cards = (
        ("已登记端点", str(len(rows)), "可供工作流选择的模型服务", "blue"),
        ("密钥已配置", str(configured), "仅表示凭据存在，连接需单独测试", "green"),
        ("已分配角色", f"{assigned} / {len(ROLE_LABELS)}", "生成与评审等工作流槽位", "purple"),
        ("预算已用", f"${spent:.2f}", f"上限 ${limit:.2f}" if limit > 0 else "尚未设置预算上限", "amber"),
    )
    st.html('<div class="df-model-summary">' + ''.join(
        '<div data-tone="' + tone + '"><small>' + _safe(label) + '</small><strong>'
        + _safe(value) + '</strong><em>' + _safe(note) + '</em></div>'
        for label, value, note, tone in cards
    ) + '</div>')
    st.html('<div class="df-model-context"><b>当前默认：' + _safe(default) + '</b>'
            '<i>›</i><span>服务端点提供模型</span><i>›</i><span>角色槽位指定用途</span>'
            '<i>›</i><span>工作流按角色调用</span><i>›</i><span>用量写入审计</span></div>')


def _panel_heading(title: str, description: str, icon: str, aside: str = "") -> None:
    st.html('<div class="df-model-panel-title"><b>' + _safe(icon) + '</b><div><strong>'
            + _safe(title) + '</strong><small>' + _safe(description) + '</small></div>'
            + ('<span>' + _safe(aside) + '</span>' if aside else '') + '</div>')


def _endpoints(rows: list[dict]) -> None:
    _panel_heading("服务端点", "查看模型、分工与凭据状态；连接可用性以实际测试为准。", "⬡", f"{len(rows)} 个端点")
    if not rows:
        st.html('<div class="df-model-empty">尚无服务端点。展开下方配置区添加第一个模型服务。</div>')
        return
    cards = []
    for row in rows:
        models = row.get("models") or []
        model_tags = ''.join('<span>' + _safe(name) + '</span>' for name in models[:3])
        if len(models) > 3:
            model_tags += '<span>+' + str(len(models) - 3) + '</span>'
        if not model_tags:
            model_tags = '<i>暂未设置模型</i>'
        assigned = row.get("roles") or []
        role_names = [ROLE_LABELS.get(role, role) for role in assigned]
        role_detail = "、".join(role_names) or "尚未分配角色"
        role_text = (("、".join(role_names[:2]) + f"等 {len(role_names)} 个角色")
                     if len(role_names) > 2 else role_detail)
        ready = _credential_ready(row)
        cards.append(
            '<div class="df-model-endpoint" data-default="' + str(bool(row.get("is_default"))).lower() + '">'
            '<div class="df-model-endpoint-head"><span class="df-model-endpoint-icon">⬡</span><strong>'
            + _safe(row.get("name", "")) + '</strong>'
            + ('<em>默认端点</em>' if row.get("is_default") else '') + '</div>'
            '<div class="df-model-endpoint-models">' + model_tags + '</div>'
            '<div class="df-model-endpoint-address" title="' + _safe(row.get("base_url", "")) + '">'
            + _safe(row.get("base_url", "")) + '</div>'
            '<div class="df-model-endpoint-foot"><span title="' + _safe(role_detail) + '">'
            + _safe(role_text) + '</span>'
            '<b data-ready="' + str(ready).lower() + '">' + ("密钥已配置" if ready else "密钥未配置") + '</b></div></div>'
        )
    st.html('<div class="df-model-endpoints">' + ''.join(cards) + '</div>')


def _endpoint_form() -> None:
    _panel_heading("配置端点", "保存到本地覆盖文件，自动保留上一个版本。", "＋")
    with st.expander("新增或覆盖端点", expanded=False):
        st.caption("推荐使用环境变量存放密钥。环境变量模式只保存变量名；手动密钥模式会写入本地配置。")
        with st.form("backend-add"):
            left, right = st.columns(2)
            name = left.text_input("后端名", "local_gpu")
            base_url = right.text_input("OpenAI 兼容地址", "http://127.0.0.1:11434/v1")
            models = st.text_input("模型名（逗号分隔）", "qwen2.5:7b-instruct")
            mode = st.radio("密钥来源", ["环境变量（推荐）", "写入本地配置"], horizontal=True)
            secret = st.text_input("密钥（环境变量名 或 密钥值）", type="password")
            prices = st.text_input("价格 JSON（可选，如 {\"input_per_1m_usd\": 0}）")
            overwrite = st.checkbox("覆盖同名后端")
            submit = st.form_submit_button("保存端点", type="primary")
        if submit:
            try:
                prices_obj = json.loads(prices) if prices.strip() else None
                model_names = [part.strip() for part in models.split(",") if part.strip()]
                if mode.startswith("环境变量"):
                    bm.save_endpoint(name, base_url, model_names, api_key_env=secret or "OPENAI_API_KEY",
                                     prices=prices_obj, explicit_replace=overwrite)
                else:
                    bm.save_endpoint(name, base_url, model_names, api_key=secret,
                                     prices=prices_obj, explicit_replace=overwrite)
                st.toast(f"已保存 {name}，原文件已备份")
                st.rerun()
            except (ValueError, FileExistsError) as error:
                st.error(str(error))


def _connection_probe(names: list[str], default: str) -> None:
    _panel_heading("连接检查", "读取所选服务的模型列表，不发起生成请求。", "↗")
    st.caption("密钥状态只表示本机配置是否存在。这里的主动检查才会访问对应端点。")
    if not names:
        st.info("添加端点后即可进行连接检查。")
        return
    target = st.selectbox("选择后端", names, index=names.index(default) if default in names else 0,
                          key="backend-test-name")
    if st.button("测试连接", type="primary", width="stretch"):
        try:
            result = bm.test_backend(target)
            models = result.get("models") or []
            st.success(f"{target} 可达，返回 {len(models)} 个模型：{', '.join(map(str, models[:12]))}")
        except Exception as error:  # noqa: BLE001 - remote SDK errors may include credentials
            st.error(f"连接失败（{type(error).__name__}）。请检查地址、密钥与服务状态。")


def _roles(roles: dict, names: list[str]) -> None:
    _panel_heading("工作流角色", "角色决定数据生成、质检和辅助阶段调用哪个端点与模型。", "◈",
                   f"{len(ROLE_LABELS)} 个槽位")
    cards = []
    for role, label in ROLE_LABELS.items():
        slot = roles.get(role) or {}
        ready = bool(slot.get("backend") and slot.get("model"))
        binding = f"{slot.get('backend')} / {slot.get('model')}" if ready else "尚未配置模型"
        cards.append('<div class="df-model-role"><b>◇</b><div><strong>' + _safe(label)
                     + '</strong><small title="' + _safe(binding) + '">' + _safe(binding)
                     + '</small></div><em data-ready="' + str(ready).lower() + '">'
                     + ('已分配' if ready else '待配置') + '</em></div>')
    st.html('<div class="df-model-role-map">' + ''.join(cards) + '</div>')


def _role_editor(roles: dict, names: list[str]) -> None:
    _panel_heading("编辑角色槽位", "保存后，后续工作流将使用新的默认模型。", "✎")
    role = st.selectbox("选择角色", list(ROLE_LABELS),
                        format_func=lambda value: ROLE_LABELS[value], key="backend-role-edit")
    slot = roles.get(role) or {}
    with st.form("role-form:" + role):
        backend = st.selectbox("后端", names, key=f"role-slot:{role}:b",
                               index=names.index(slot["backend"]) if slot.get("backend") in names else 0)
        model = st.text_input("模型", slot.get("model", ""), key=f"role-slot:{role}:m")
        save = st.form_submit_button("保存角色", type="primary", disabled=not names, width="stretch")
    if save:
        try:
            bm.set_role(role, backend, model)
            st.toast(f"{ROLE_LABELS[role]}已更新")
            st.rerun()
        except ValueError as error:
            st.error(str(error))


def _budget(info: dict) -> None:
    limit = float((info.get("budget") or {}).get("max_total_usd") or 0)
    spent = float(info.get("spent") or 0)
    remaining = max(0.0, limit - spent)
    ratio = min(1.0, spent / limit) if limit > 0 else 0.0
    if limit > 0:
        headline = f"${spent:.4f} / ${limit:.2f}"
        detail = f"剩余额度 ${remaining:.4f}"
    else:
        headline = f"${spent:.4f}"
        detail = "当前未设置预算上限"
    usage, actions = st.columns([1.45, 1], gap="medium")
    with usage, st.container(border=True, key="model-budget-usage"):
        _panel_heading("预算与用量", "已用额度读取磁盘审计记录。", "◷")
        st.html('<div class="df-model-budget"><div class="df-model-budget-top"><div><small>累计已用</small>'
                '<strong>' + _safe(headline) + '</strong><em>' + _safe(detail) + '</em></div>'
                '<div><small>预算状态</small><strong>' + (f"{ratio * 100:.1f}%" if limit > 0 else "—")
                + '</strong></div></div><div class="df-model-budget-track"><i style="width:'
                + f"{ratio * 100:.1f}%" + '"></i></div><div class="df-model-budget-notes"><span>已使用</span>'
                '<span>' + ("预算上限" if limit > 0 else "未设上限") + '</span></div></div>')
    with actions, st.container(border=True, key="model-budget-actions"):
        _panel_heading("预算操作", "清零会留下审计记录。", "↻")
        st.caption("仅重置本地累计额度，不会修改外部服务账单或预算上限。")
        confirm = st.checkbox("我确认清零预算（审计记录本次操作）", key="budget-reset-confirm")
        if st.button("清零预算", disabled=not confirm, width="stretch"):
            previous = bm.reset_budget("console")
            st.success(f"预算已清零（原已用 ${previous:.4f}，已记审计）")


def render_backend_page() -> None:
    """Render real, masked backend inventory and editable workflow bindings."""
    page_header("模型与密钥", "配置工作流调用的模型服务、角色槽位与预算；密钥只展示配置状态。", "MODEL CONNECTIONS")
    st.html(CSS)
    info = bm.list_backends()
    rows = info.get("backends") or []
    names = [row["name"] for row in rows]
    _summary(info)
    endpoint_tab, role_tab, budget_tab = st.tabs(["端点与连接", "角色槽位", "预算与用量"])
    with endpoint_tab:
        with st.container(border=True):
            _endpoints(rows)
        left, right = st.columns([1.35, 1], gap="medium")
        with left, st.container(border=True):
            _endpoint_form()
        with right, st.container(border=True):
            _connection_probe(names, info.get("default_backend", ""))
    with role_tab:
        left, right = st.columns([1.35, 1], gap="medium")
        with left, st.container(border=True):
            _roles(info.get("roles") or {}, names)
        with right, st.container(border=True):
            _role_editor(info.get("roles") or {}, names)
    with budget_tab:
        _budget(info)
