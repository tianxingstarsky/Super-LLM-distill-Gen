"""Configure each node's models and service connection in the node panel."""
from copy import deepcopy
import hashlib
import json

import streamlit as st
from filelock import Timeout

from lib.application.backend_service import BackendApplication
from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.domain.workflow_scale import (DEFAULT_CONTEXT_WINDOW_TOKENS,
                                       DEFAULT_MAX_OUTPUT_TOKENS,
                                       MAX_CONTEXT_WINDOW_TOKENS, NODE_ROLES, node_roles,
                                       validate_node_models)
from lib.model_protocols import API_FORMATS
from lib.presentation.streamlit.i18n import UntranslatedText
from lib.presentation.streamlit.workflow_model_capabilities import (
    discovered_choices, model_info, render_model_capabilities,
    render_model_discovery, suggested_tokens)


_API_FORMAT_LABELS = {"chat": "Chat Completions", "responses": "OpenAI Responses",
                      "anthropic": "Anthropic Messages"}
_PROCESS_REVIEW_DESCRIPTIONS = {
    "sft": "另发模型请求，核对回答是否符合来源与任务要求；可沿用生成模型。",
    "multiturn": "另发模型请求，核对每轮回答与整段对话的一致性；可沿用生成模型。",
    "cot": "另发模型请求，核对推理是否支持答案；可沿用生成模型。",
    "trim": "另发模型请求，核对答案是否保留、清理规则是否满足；可沿用生成模型。",
    "cpt": "另发模型请求，核对事实、数字与公式是否保真；可沿用清洗模型。",
    "preference": "另发模型请求，比较两个候选回答并构造偏好；可沿用生成模型。",
}
_PROCESS_REVIEW_HELP = "这是用于过程核对的普通大模型，并非特殊模型。可选 JEV 评分不替代这些核对。"


def node_bindings(application: WorkflowNodeModelsApplication, nodes, source_mode, workspace, *, node_generation=None, package_review=None, cpt_processing=None):
    draft_key = f"workflow-node-bindings:{workspace}"
    saved = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(draft_key, {})
    draft = deepcopy(st.session_state.get(draft_key, saved))
    initialized_key = draft_key + ":initialized"
    # An explicitly emptied role stays empty after restart. Keep references
    # to temporarily hidden nodes so switching targets never discards edits.
    # Terminal review can be switched off while its explicit empty choice is
    # saved. Its remembered role must not depend on the current switch value.
    restored_markers = [node + ":" + role for node in draft for role in (
        NODE_ROLES[node] if node in {"jev", "package"} else node_roles(
            node, source_mode, node_generation=node_generation, package_review=package_review,
            cpt_processing=cpt_processing))]
    draft, initialized, endpoints = application.prepare_draft(
        nodes, source_mode, draft, st.session_state.get(initialized_key, restored_markers),
        node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing)
    st.session_state[draft_key] = draft
    st.session_state[initialized_key] = sorted(initialized)
    st.session_state[f"workflow-node-scope:{workspace}"] = {
        "nodes": list(nodes), "source_mode": source_mode,
        "node_generation": deepcopy(node_generation), "package_review": deepcopy(package_review),
        "cpt_processing": deepcopy(cpt_processing),
    }
    agent_key = f"workflow-agent-mode:{workspace}"
    if agent_key not in st.session_state:
        st.session_state[agent_key] = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(agent_key, "local")
    _persist_bindings(workspace, draft)
    return draft, endpoints


def _persist_draft_value(workspace: str, key: str, value) -> None:
    """Autosave this session's complete form, including prompt edits."""
    form_key = f"workflow-form-draft:{workspace}"
    previous = st.session_state.get(form_key, {})
    changed = previous.get(key) != value or key not in previous
    if changed:
        st.session_state[form_key] = {**previous, key: deepcopy(value)}
    application = st.session_state.get(f"workflow-draft-application:{workspace}")
    if application is not None and (changed or st.session_state.get(f"workflow-draft-error:{workspace}")):
        try:
            application.replace(st.session_state[form_key])
        except (OSError, ValueError, Timeout):
            st.session_state[f"workflow-draft-error:{workspace}"] = True
        else:
            st.session_state.pop(f"workflow-draft-error:{workspace}", None)


def _persist_bindings(workspace: str, bindings: dict) -> None:
    draft_key = f"workflow-node-bindings:{workspace}"
    safe = validate_node_models(bindings)
    confirmation_key = f"workflow-node-model-confirmations:{workspace}"
    confirmations = _model_confirmations(workspace)
    previous = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(draft_key, {})
    try:
        previous = validate_node_models(previous)
    except ValueError:
        previous = {}
    for node in set(previous) | set(safe):
        if previous.get(node) != safe.get(node):
            confirmations.pop(node, None)
    st.session_state[confirmation_key] = confirmations
    _persist_draft_value(workspace, confirmation_key, confirmations)
    st.session_state[draft_key] = deepcopy(safe)
    _persist_draft_value(workspace, draft_key, safe)


def _model_fingerprint(node: str, bindings: dict) -> str:
    normalized = validate_node_models({node: bindings})[node]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _model_confirmations(workspace: str) -> dict:
    """Migrate existing saved choices once; new autosaved choices stay pending."""
    key = f"workflow-node-model-confirmations:{workspace}"
    if key not in st.session_state:
        form = st.session_state.get(f"workflow-form-draft:{workspace}", {})
        if key in form:
            confirmations = deepcopy(form[key])
        else:
            confirmations = {}
            for node, bindings in form.get(f"workflow-node-bindings:{workspace}", {}).items():
                if bindings:
                    try:
                        confirmations[node] = _model_fingerprint(node, bindings)
                    except ValueError:
                        pass
        st.session_state[key] = confirmations
    return deepcopy(st.session_state[key])


def _confirmation_ready(node: str, roles: tuple[str, ...], bindings: dict, endpoints) -> bool:
    try:
        normalized = validate_node_models({node: bindings})[node]
    except ValueError:
        return False
    return bool(roles) and all(role in normalized and normalized[role]["backend"] in endpoints for role in roles)


def _confirm_node_models(workspace: str, node: str, roles: tuple[str, ...], endpoints,
                         application: BackendApplication | None) -> None:
    """Confirm the current durable draft, never a stale render-time binding."""
    if application is not None:
        try:
            endpoints = {item["name"] for item in application.list_backends().get("backends", [])}
        except (OSError, ValueError, RuntimeError):
            st.toast("暂时无法核对模型服务，请稍后重试。")
            return
    bindings = st.session_state.get(f"workflow-node-bindings:{workspace}", {}).get(node, {})
    if not _confirmation_ready(node, roles, bindings, endpoints):
        st.toast("请先为当前节点补全有效的模型配置。")
        return
    key = f"workflow-node-model-confirmations:{workspace}"
    confirmations = _model_confirmations(workspace)
    confirmations[node] = _model_fingerprint(node, bindings)
    st.session_state[key] = confirmations
    _persist_draft_value(workspace, key, confirmations)
    st.toast("模型配置尚未保存，请重试确认。" if st.session_state.get(f"workflow-draft-error:{workspace}")
             else "当前节点的模型配置已确认。")


def _render_model_confirmation(node: str, roles: tuple[str, ...], workspace: str,
                               bindings: dict, endpoints: dict, application) -> None:
    node_bindings = bindings.get(node, {})
    ready = _confirmation_ready(node, roles, node_bindings, endpoints)
    confirmed = (ready and _model_confirmations(workspace).get(node) == _model_fingerprint(node, node_bindings)
                 and not st.session_state.get(f"workflow-draft-error:{workspace}"))
    status, action = st.columns([1.7, 1], gap="small", vertical_alignment="center")
    with status:
        st.caption("已确认 · 配置已保存" if confirmed else
                   "待确认 · 草稿尚未保存" if st.session_state.get(f"workflow-draft-error:{workspace}") else
                   "待确认 · 修改已自动保存为草稿" if ready else "待确认 · 请补全模型配置")
    with action:
        st.button("确认模型配置", key=f"node-model-confirm:{workspace}:{node}",
                  disabled=not ready or confirmed, type="secondary" if confirmed else "primary",
                  on_click=_confirm_node_models,
                  args=(workspace, node, roles, tuple(endpoints), application), width="stretch",
                  help="确认当前节点的模型与 token 上限，不会发起模型请求。")


def _set_binding_widgets(workspace: str, node: str, role: str, binding: dict) -> None:
    prefix = f"node-model:{workspace}:{node}:{role}"
    backend, model = binding["backend"], binding["model"]
    st.session_state[prefix + ":backend"] = backend
    st.session_state[prefix + ":model:" + backend] = model
    st.session_state[prefix + ":context:" + backend + ":" + model] = binding["context_window_tokens"]
    st.session_state[prefix + ":output:" + backend + ":" + model] = binding["max_output_tokens"]


def _save_model_selection(workspace: str, node: str, role: str, application=None) -> None:
    """Capture widget edits before a simultaneous node switch hides them."""
    draft = deepcopy(st.session_state.get(f"workflow-node-bindings:{workspace}", {}))
    previous = draft.get(node, {}).get(role, {})
    prefix = f"node-model:{workspace}:{node}:{role}"
    backend = st.session_state.get(prefix + ":backend")
    model = st.session_state.get(prefix + ":model:" + str(backend))
    if isinstance(model, str):
        model = model.strip()
    if backend and model:
        same_model = previous.get("backend") == backend and previous.get("model") == model
        suggested_context, suggested_output = suggested_tokens(model_info(application, backend, model))
        binding = {"backend": backend, "model": model,
                   "context_window_tokens": st.session_state.get(
                       prefix + ":context:" + backend + ":" + model,
                       previous.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
                       if same_model else suggested_context),
                   "max_output_tokens": st.session_state.get(
                       prefix + ":output:" + backend + ":" + model,
                       previous.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
                       if same_model else suggested_output)}
        try:
            binding = validate_node_models({node: {role: binding}})[node][role]
        except ValueError:
            draft.setdefault(node, {}).pop(role, None)
        else:
            draft.setdefault(node, {})[role] = binding
    else:
        draft.setdefault(node, {}).pop(role, None)
    _persist_bindings(workspace, draft)


def _missing_role_copies(node: str, workspace: str, bindings: dict, endpoints: dict):
    scope = st.session_state.get(f"workflow-node-scope:{workspace}", {})
    copies = []
    for target in scope.get("nodes", []):
        if target == node:
            continue
        for role in node_roles(target, scope["source_mode"],
                               node_generation=scope.get("node_generation"),
                               package_review=scope.get("package_review"), cpt_processing=scope.get("cpt_processing")):
            source = bindings.get(node, {}).get(role, {})
            if (not bindings.get(target, {}).get(role) and source.get("backend") in endpoints
                    and source.get("model")):
                copies.append((target, role, source))
    return copies


def _fill_missing_models(workspace: str, copies) -> None:
    draft_key = f"workflow-node-bindings:{workspace}"
    draft = deepcopy(st.session_state.get(draft_key, {}))
    for node, role, binding in copies:
        # Recheck on click, including an edit that happened after rendering.
        if draft.get(node, {}).get(role):
            continue
        copied = deepcopy(binding)
        draft.setdefault(node, {})[role] = copied
        _set_binding_widgets(workspace, node, role, copied)
    _persist_bindings(workspace, draft)


def _reusable_bindings(node: str, role: str, bindings: dict, endpoints: dict) -> list[tuple[str, dict]]:
    """Offer only usable models already chosen for the same role on another node."""
    current = bindings.get(node, {}).get(role, {})
    seen = {(current.get("backend"), current.get("model"),
             current.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS),
             current.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS))}
    choices = []
    for source, roles in bindings.items():
        if source == node:
            continue
        binding = roles.get(role, {})
        backend = binding.get("backend")
        if backend not in endpoints or not binding.get("model"):
            continue
        signature = (backend, binding["model"],
                     binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS),
                     binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS))
        if signature in seen:
            continue
        seen.add(signature)
        choices.append((source, binding))
    return choices


def _reuse_binding(workspace: str, node: str, role: str, binding: dict) -> None:
    """Copy an explicit choice into one node without changing the source node."""
    draft_key = f"workflow-node-bindings:{workspace}"
    draft = deepcopy(st.session_state.get(draft_key, {}))
    copied = validate_node_models({node: {role: binding}})[node][role]
    draft.setdefault(node, {})[role] = copied
    _set_binding_widgets(workspace, node, role, copied)
    _persist_bindings(workspace, draft)


def _render_model_reuse(node: str, role: str, workspace: str, bindings: dict, endpoints: dict, *,
                        allow_generation_reuse: bool) -> None:
    """Keep reuse explicit, including replacement of an existing role choice."""
    binding = bindings.get(node, {}).get(role, {})
    prefix = f"node-model:{workspace}:{node}:{role}"
    writer = bindings.get(node, {}).get("generation", {})
    if (allow_generation_reuse and role == "jev" and writer.get("backend") in endpoints and writer.get("model")
            and writer != binding):
        st.button("沿用本节点生成模型", key=prefix + ":reuse-generation",
                  on_click=_reuse_binding, args=(workspace, node, role, deepcopy(writer)),
                  help="复制生成模型及 token 上限到评审配置；只在点击时替换，之后可分别调整。",
                  width="stretch")
    choices = _reusable_bindings(node, role, bindings, endpoints)
    if not choices:
        return
    help_text = "只复制到当前角色。模型服务、模型和 token 上限可继续分别调整。"
    if not binding and len(choices) == 1:
        source, reusable = choices[0]
        st.button(f"沿用 {source.upper()} 节点的 {reusable['model']}", key=prefix + ":reuse:" + source,
                  on_click=_reuse_binding, args=(workspace, node, role, deepcopy(reusable)),
                  help=help_text, width="stretch")
        return
    with st.popover("复用其他节点模型", width="stretch"):
        candidates = dict(choices)
        source_key = prefix + ":reuse-source"
        if source_key in st.session_state and st.session_state[source_key] not in candidates:
            st.session_state.pop(source_key)
        source = st.selectbox("已有模型配置", list(candidates), key=source_key,
                              format_func=lambda source: UntranslatedText(
                                  source.upper() + " · " + candidates[source]["backend"]
                                  + " · " + candidates[source]["model"]))
        st.button("用于当前角色", key=prefix + ":reuse-apply",
                  on_click=_reuse_binding, args=(workspace, node, role, deepcopy(candidates[source])),
                  help=help_text, width="stretch")


def _connect_service(node: str, workspace: str, roles: tuple[str, ...], bindings: dict,
                     endpoints: dict, application: BackendApplication | None) -> None:
    if application is None:
        return
    form_prefix = f"node-service-connect:{workspace}:{node}"
    old_secret_key = st.session_state.pop(form_prefix + ":clear-secret", None)
    if old_secret_key:
        st.session_state.pop(old_secret_key, None)
    epoch = int(st.session_state.get(form_prefix + ":epoch", 0))
    missing_role = next((role for role in roles if not bindings.get(node, {}).get(role, {}).get("model")
                         or bindings[node][role].get("backend") not in endpoints), None)
    # Adding a connection beside existing role controls makes a very long
    # inspector. A popover keeps that occasional edit on the selected node.
    # For the first connection, show the form directly to avoid another click.
    connection_panel = (st.popover("新增或更新服务连接", width="stretch") if endpoints
                        else st.expander("新增或更新服务连接", expanded=True))
    with connection_panel:
        st.caption("连接保存在本机供复用；本次任务使用哪个模型由当前节点决定。")
        with st.form(f"{form_prefix}:{epoch}", clear_on_submit=False):
            name = st.text_input("服务名称", placeholder="字母、数字、下划线或连字符")
            api_format = st.selectbox("API 协议", API_FORMATS,
                                      format_func=lambda value: _API_FORMAT_LABELS[value],
                                      help="选择服务实际支持的调用协议。")
            base_url = st.text_input("服务 API 地址", placeholder="如 https://api.openai.com/v1 或 https://api.anthropic.com")
            model = st.text_input("模型名称", placeholder="填写服务提供的模型名")
            credential_mode = st.radio("凭据来源", ("环境变量（推荐）", "直接填写密钥"),
                                       horizontal=True,
                                       help="直接填写的密钥会保存到本机配置；推荐使用环境变量。")
            secret_key = f"{form_prefix}:secret:{epoch}"
            secret = st.text_input("环境变量名或密钥", type="password", key=secret_key,
                                   placeholder="环境变量留空时使用 ANTHROPIC_API_KEY" if api_format == "anthropic"
                                   else "环境变量留空时使用 OPENAI_API_KEY")
            discovered_key = form_prefix + ":discovered"
            discovered = st.session_state.get(discovered_key) or {}
            current_connection = (base_url.strip(), api_format, credential_mode, secret)
            # A connection fingerprint excludes the secret itself from cached
            # results while invalidating choices when form credentials change.
            connection_key = hashlib.sha256(repr(current_connection).encode()).hexdigest()
            choices = ((discovered.get("models") or [])
                       if discovered.get("connection") == connection_key else [])
            selected_discovered = st.selectbox("获取到的模型", choices, index=None,
                                               key=f"{form_prefix}:discovered-choice:{epoch}",
                                               placeholder="先获取模型，或手动填写模型名称") if choices else None
            if discovered.get("connection") == connection_key and discovered.get("truncated"):
                st.warning("模型列表过长或响应超时，仅显示已获取的模型。")
            discover = (st.form_submit_button("获取模型", width="stretch")
                        if hasattr(application, "discover_models") else False)
            st.caption("硬预算需要输入、输出两项单价。按服务商报价填写每百万 tokens 的美元价格；一个连接有多个模型时按最高价填写。")
            price_columns = st.columns(2, gap="small")
            with price_columns[0]:
                input_price = st.number_input("输入单价（美元 / 百万 tokens）", min_value=0.0,
                                              value=None, step=0.01, format="%.4f")
            with price_columns[1]:
                output_price = st.number_input("输出单价（美元 / 百万 tokens）", min_value=0.0,
                                               value=None, step=0.01, format="%.4f")
            free_service = st.checkbox("此服务明确无需按 token 计费")
            replace = st.checkbox("覆盖同名连接")
            submit = st.form_submit_button("保存并用于当前节点" if missing_role else "保存连接",
                                           type="primary", width="stretch")
        if discover:
            if credential_mode == "直接填写密钥" and not secret:
                st.error("请填写密钥，或改用环境变量。")
                return
            overrides = {"base_url": base_url.strip(), "api_format": api_format, "models": []}
            if credential_mode == "直接填写密钥":
                overrides["api_key"] = secret
            else:
                overrides["api_key_env"] = secret.strip() or ("ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY")
            try:
                with st.spinner("正在获取模型列表…"):
                    result = application.discover_models(name.strip() or "preview", overrides=overrides)
            except (OSError, ValueError, RuntimeError):
                st.warning("未能获取模型列表，请检查地址、协议和凭据。仍可手动输入模型名。")
            else:
                if result.get("ok", True) and isinstance(result.get("models"), list):
                    st.session_state[discovered_key] = {"models": result["models"], "connection": connection_key,
                                                        "model_info": deepcopy(result.get("model_info") or {}),
                                                        "truncated": bool(result.get("truncated"))}
                    st.rerun()
                else:
                    st.warning("未能获取模型列表，请检查地址、协议和凭据。仍可手动输入模型名。")
            return
        if not submit:
            return
        model = model.strip() or selected_discovered or ""
        if not model.strip() or len(model) > 200 or any(ord(character) < 32 for character in model):
            st.error("请先选择或填写有效模型名称。")
            return
        if credential_mode == "直接填写密钥" and not secret:
            st.error("请填写密钥，或改用环境变量。")
            return
        if not free_service and (input_price is None or output_price is None
                                 or input_price + output_price <= 0):
            st.error("请填写输入和输出单价；明确免费的服务可勾选无需计费。")
            return
        prices = {"input_per_1m_usd": 0.0 if free_service else input_price,
                  "output_per_1m_usd": 0.0 if free_service else output_price}
        try:
            default_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
            if credential_mode == "直接填写密钥":
                application.save_endpoint(name.strip(), base_url.strip(), [model.strip()],
                                          api_key=secret, prices=prices,
                                          explicit_replace=replace, api_format=api_format)
            else:
                application.save_endpoint(name.strip(), base_url.strip(), [model.strip()],
                                          api_key_env=secret.strip() or default_env,
                                          prices=prices,
                                          explicit_replace=replace, api_format=api_format)
        except FileExistsError:
            st.error("服务名称已存在。如需更新，请勾选覆盖同名连接。")
            return
        except ValueError:
            st.error("连接信息无效。请检查服务名称、地址和模型名。")
            return
        except OSError:
            st.error("保存连接失败。请检查本机配置是否可写。")
            return
        # Fill only a missing role. Adding a connection must not silently
        # replace a model the operator already selected on this node.
        target_role = missing_role
        if target_role is not None:
            info = model_info(application, name.strip(), model.strip())
            if discovered.get("connection") == connection_key:
                temporary_info = (discovered.get("model_info") or {}).get(model.strip()) or {}
                info = {**info, **{field: value for field, value in temporary_info.items() if value is not None}}
            context_tokens, output_tokens = suggested_tokens(info)
            bindings.setdefault(node, {})[target_role] = {
                "backend": name.strip(), "model": model.strip(),
                "context_window_tokens": context_tokens,
                "max_output_tokens": output_tokens,
            }
            _persist_bindings(workspace, bindings)
            st.session_state[f"workflow-node-pending-binding:{workspace}:{node}"] = {
                "role": target_role, "backend": name.strip(), "model": model.strip(),
            }
        st.session_state[form_prefix + ":clear-secret"] = secret_key
        st.session_state[form_prefix + ":epoch"] = epoch + 1
        st.toast("连接已保存，并已用于当前节点。" if target_role else "连接已保存；可在当前节点选择使用。")
        st.rerun()


def render_node_models(node, source_mode, workspace, bindings, endpoints, *,
                       backend_application: BackendApplication | None = None, node_generation=None, package_review=None, cpt_processing=None):
    pending = st.session_state.pop(f"workflow-node-pending-binding:{workspace}:{node}", None)
    if pending and pending.get("role") in node_roles(node, source_mode, node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing):
        prefix = f"node-model:{workspace}:{node}:{pending['role']}"
        st.session_state[prefix + ":backend"] = pending["backend"]
        st.session_state[prefix + ":model:" + pending["backend"]] = pending["model"]
    previous = deepcopy(bindings)
    roles = node_roles(node, source_mode, node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing)
    if not roles:
        explanation = {
            "ingest": "解析上传来源并保留来源位置；此步骤不调用生成模型。",
            "cpt": "清洗、分块并去重已有语料；此步骤不调用生成模型。",
            "agent": "核对已记录的工具轨迹；此节点不调用模型。验证环境在下方选择。",
            "gsm8k": "生成可复现的多步整数算术题，核对计算标注和最终答案；不处理通用数学证明，此节点不调用模型。",
            "package": ("检查输出规则并整理产物，不调用模型。"
                        if (package_review or {}).get("node") == "jev" else
                        "核对产物清单并整理候选数据；可开启 AI 评审，再选择本节点的评审模型。"),
            "jev": "未启用额外 JEV 评分；各生成节点仍按配置进行过程核对。",
        }.get(node, "此节点不需要配置模型。")
        st.info(explanation)
        return
    if not endpoints:
        if any(bindings.get(node, {}).get(role) for role in roles):
            st.warning("已保存的模型连接不可用，请重新选择；原选择保留在草稿中。")
        st.info("当前没有可用的模型连接。请在下方登记服务地址和模型。")
        _connect_service(node, workspace, roles, bindings, endpoints, backend_application)
        return
    _render_model_confirmation(node, roles, workspace, bindings, endpoints, backend_application)
    for role in roles:
        writer_label = ("清洗模型" if node == "cpt" and source_mode != "开放需求" else
                        "文档解析模型" if node == "ingest" and source_mode == "模型辅助文档" else "生成模型")
        review_label = ("JEV 评分模型" if node == "jev" else
                        "质量评审模型" if node == "package" else "过程核对模型")
        st.html('<p style="font-size:14px;margin:14px 0 8px"><strong>'
                + ("多模态识别模型" if role == "vision" else writer_label if role == "generation" else review_label) + '</strong></p>')
        if role == "jev":
            if node == "jev":
                st.caption("可选的最终质量评分，会另发模型请求；关闭后仍保留各节点的过程核对。",
                           help="JEV 是评分步骤，不是模型名称；可选择普通大模型。")
            elif node == "package":
                st.caption("打包前另发模型请求评估最终样本；可选择普通大模型。")
            else:
                st.caption(_PROCESS_REVIEW_DESCRIPTIONS.get(node,
                    "另发模型请求核对当前步骤的结果；可沿用生成模型。"), help=_PROCESS_REVIEW_HELP)
        binding = bindings.get(node, {}).get(role, {})
        prefix = f"node-model:{workspace}:{node}:{role}"
        _render_model_reuse(node, role, workspace, bindings, endpoints,
                            allow_generation_reuse="generation" in roles)
        names = list(endpoints)
        if binding.get("backend") and binding["backend"] not in endpoints:
            st.warning("已保存的模型连接不可用，请重新选择；原选择保留在草稿中。")
            st.caption(UntranslatedText(binding["backend"] + " · " + binding.get("model", "")))
        if prefix + ":backend" in st.session_state and st.session_state[prefix + ":backend"] not in names:
            st.session_state[prefix + ":backend"] = None
        if prefix + ":backend" not in st.session_state:
            st.session_state[prefix + ":backend"] = binding.get("backend") if binding.get("backend") in names else None
        service_column, discovery_column = st.columns([3, 1], gap="small", vertical_alignment="bottom")
        with service_column:
            backend = st.selectbox("模型服务", names, index=None, key=prefix + ":backend",
                                   placeholder="选择模型服务",
                                   on_change=_save_model_selection, args=(workspace, node, role, backend_application),
                                   format_func=lambda name: name + " · " + _API_FORMAT_LABELS.get(
                                       endpoints[name].get("api_format", "chat"), "Chat Completions"))
        if backend is not None:
            with discovery_column:
                endpoints[backend] = render_model_discovery(backend_application, backend, endpoints[backend], key=prefix + ":discover")
        if backend is None:
            if binding.get("backend") in endpoints:
                bindings.setdefault(node, {}).pop(role, None)
            continue
        models = discovered_choices(endpoints[backend])
        if binding.get("backend") == backend and binding.get("model") not in models and binding.get("model"):
            models.append(binding["model"])
        model_key = prefix + ":model:" + backend
        if model_key not in st.session_state:
            st.session_state[model_key] = binding.get("model") if binding.get("backend") == backend else None
        # A non-empty default index makes Streamlit deserialize a clear action
        # back to that default. Restore through state so None remains explicit.
        model = st.selectbox("模型", models, index=None,
                             accept_new_options=True, key=model_key,
                             on_change=_save_model_selection, args=(workspace, node, role, backend_application),
                             placeholder="选择或输入模型名")
        if model:
            if (len(model) > 200 or not model.strip() or any(ord(character) < 32 for character in model)):
                st.warning("模型名称无效，请填写不超过 200 个字符的模型名。")
                bindings.setdefault(node, {}).pop(role, None)
                continue
            model = model.strip()
            info = model_info(backend_application, backend, model, endpoints[backend])
            same_model = binding.get("backend") == backend and binding.get("model") == model
            suggested_context, suggested_output = suggested_tokens(info)
            context_default = (binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
                               if same_model else suggested_context)
            output_default = (binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
                              if same_model else suggested_output)
            context_column, output_column = st.columns(2, gap="small")
            with context_column:
                context_tokens = st.number_input(
                    "上下文窗口（tokens）", min_value=1, max_value=MAX_CONTEXT_WINDOW_TOKENS,
                    value=int(context_default), step=1024,
                    key=prefix + ":context:" + backend + ":" + model,
                    on_change=_save_model_selection, args=(workspace, node, role, backend_application),
                )
            with output_column:
                output_tokens = st.number_input(
                    "单次输出上限（tokens）", min_value=1, max_value=MAX_CONTEXT_WINDOW_TOKENS - 1,
                    value=int(output_default), step=1024,
                    key=prefix + ":output:" + backend + ":" + model,
                    on_change=_save_model_selection, args=(workspace, node, role, backend_application),
                )
            if output_tokens >= context_tokens:
                st.warning("单次输出上限必须小于上下文窗口。")
                bindings.setdefault(node, {}).pop(role, None)
                continue
            bindings.setdefault(node, {})[role] = {
                "backend": backend, "model": model,
                "context_window_tokens": int(context_tokens),
                "max_output_tokens": int(output_tokens),
            }
            render_model_capabilities(backend_application, backend, model, info, prefix=prefix,
                                      save_selection=_save_model_selection,
                                      save_args=(workspace, node, role, backend_application))
        else:
            bindings.setdefault(node, {}).pop(role, None)
    _persist_bindings(workspace, bindings)
    copies = _missing_role_copies(node, workspace, bindings, endpoints)
    if copies:
        targets = list(dict.fromkeys(target.upper() for target, _, _ in copies))
        st.button("填充其他节点未配置的模型", key=f"node-model-fill:{workspace}:{node}",
                  on_click=_fill_missing_models, args=(workspace, deepcopy(copies)),
                  help="按相同角色复制当前节点的模型与 token 上限。保留已有选择，之后仍可逐个修改。",
                  width="stretch")
        st.caption(UntranslatedText("、".join(targets)))
    st.caption("同一模型可用于多个节点，也可同时用于生成和评审；各角色的参数分别保存。")
    st.caption("模型和 token 上限随草稿保存在本机；默认上下文 131,072、单次输出 32,768 tokens。开始运行后，本次配置固定。")
    _connect_service(node, workspace, roles, bindings, endpoints, backend_application)
    if bindings != previous:
        st.rerun()


def snapshot_available_bindings(nodes, source_mode, bindings, endpoints, *, node_generation=None, package_review=None, cpt_processing=None):
    return {node: {role: deepcopy(bindings[node][role]) for role in node_roles(node, source_mode, node_generation=node_generation, package_review=package_review, cpt_processing=cpt_processing)
                   if role in bindings.get(node, {}) and bindings[node][role]["backend"] in endpoints} for node in nodes}


def document_parser_mode(workspace):
    """Keep the chosen parser when Streamlit removes an unrendered widget."""
    draft_key = f"workflow-document-parse-mode-draft:{workspace}"
    key = f"workflow-document-parse-mode:{workspace}"
    saved = st.session_state.get(f"workflow-form-draft:{workspace}", {}).get(key, "model")
    mode = st.session_state.get(draft_key, st.session_state.get(key, saved))
    if mode not in {"native", "model", "vision"}:
        mode = "model"
    st.session_state[draft_key] = mode
    return mode


def _save_document_parser_mode(workspace):
    mode = st.session_state.get(f"workflow-document-parse-mode:{workspace}", "native")
    if mode in {"native", "model", "vision"}:
        st.session_state[f"workflow-document-parse-mode-draft:{workspace}"] = mode
        _persist_draft_value(workspace, f"workflow-document-parse-mode:{workspace}", mode)


def render_document_parser(workspace, bindings, endpoints, *, backend_application=None):
    """Configure document reading on the input node and require explicit vision support."""
    from lib.domain.document_parser import supports_vision, vision_connection_signature
    key = f"workflow-document-parse-mode:{workspace}"
    saved_mode = document_parser_mode(workspace)
    if key not in st.session_state:
        st.session_state[key] = saved_mode
    mode = st.radio("文档解析方式", ("model", "vision", "native"), horizontal=True, key=key,
                    on_change=_save_document_parser_mode, args=(workspace,),
                    format_func=lambda value: {"native": "本地文本解析", "model": "模型辅助解析", "vision": "多模态识别"}[value])
    if mode == "native":
        st.caption("读取文档文字，不调用模型。扫描 PDF 与图片请使用多模态识别。")
        return {"mode": "native"}
    if mode == "model":
        st.caption("PDF 逐页提取文字后交给模型整理；保留数字、公式与表格关系。扫描页和图片请切换多模态识别。")
        render_node_models("ingest", "模型辅助文档", workspace, bindings, endpoints,
                           backend_application=backend_application)
        binding = bindings.get("ingest", {}).get("generation")
        return {"mode": "model", **({"binding": deepcopy(binding)} if binding else {})}
    st.caption("PDF 按页识别，图片直接识别，DOCX 保留文字并识别内嵌图片。所选资料会发送至节点模型服务。")
    render_node_models("ingest", "多模态文档", workspace, bindings, endpoints,
                       backend_application=backend_application)
    binding = bindings.get("ingest", {}).get("vision")
    if not binding:
        return {"mode": "vision"}
    endpoint = endpoints.get(binding["backend"], {})
    if not supports_vision(endpoint, binding["model"]):
        confirmation = st.checkbox("我已核实所选模型支持图片输入", key=(
            f"node-vision-confirm:{workspace}:{binding['backend']}:{binding['model']}:"
            f"{vision_connection_signature(endpoint)}"))
        if st.button("保存多模态能力确认", disabled=not confirmation or backend_application is None,
                     key=f"node-vision-save:{workspace}"):
            try:
                backend_application.confirm_model_vision(binding["backend"], binding["model"], True)
            except (OSError, ValueError):
                st.error("模型能力确认未保存，请检查本机配置后重试。")
            else:
                st.rerun()
        st.warning("此模型尚未确认图片输入能力，无法开始多模态识别。模型名称不作为能力判断依据。")
        return {"mode": "vision", "binding": deepcopy(binding), "unconfirmed": True}
    st.caption("图片输入能力已确认，识别结果仍需核对原文。")
    return {"mode": "vision", "binding": deepcopy(binding)}


def render_agent_verification(workspace, capabilities, check_environment=None):
    draft_key = f"workflow-agent-mode:{workspace}"
    draft = st.session_state.get(draft_key, st.session_state.get(
        f"workflow-form-draft:{workspace}", {}).get(draft_key, "local"))
    mode = st.selectbox("轨迹验证方式", ["local", "isolated"], index=1 if draft == "isolated" else 0,
                        format_func=lambda value: "本地验证" if value == "local" else "隔离验证",
                        key=f"agent-mode-choice:{workspace}")
    st.session_state[draft_key] = mode
    _persist_draft_value(workspace, draft_key, mode)
    if mode == "isolated":
        if not capabilities.get("isolated_configured"):
            st.warning("尚未配置可用的隔离验证环境。选择本地验证，或完成环境配置后再开始。")
        else:
            st.caption("已登记固定版本的隔离镜像；运行时仍会检查本机环境。")
        st.caption("隔离验证增加受限账本工具的状态重放，不运行上传记录中的任意代码。")
        if check_environment and st.button("检查隔离环境", key=f"agent-check:{workspace}"):
            with st.spinner("正在检查本机容器服务与镜像…"):
                result = check_environment()
            labels = {
                "ready": "容器服务与固定镜像可用；实际重放仍需在运行时验证。",
                "invalid_image": "隔离镜像配置无效，请检查固定版本设置。",
                "image_not_configured": "尚未配置固定版本的隔离镜像。",
                "daemon_unavailable": "容器服务未启动或无法连接，请先启动 Docker。",
                "linux_required": "隔离验证需要 Linux 容器服务。",
                "image_missing": "本机尚未准备配置中的固定镜像。",
                "image_platform_mismatch": "镜像平台不匹配，需要 Linux amd64 镜像。",
                "docker_missing": "本机未找到 Docker 命令。",
                "check_unavailable": "环境检查未完成，请检查容器服务后重试。",
            }
            message = labels.get(result.get("reason"), labels["check_unavailable"])
            (st.success if result.get("ready") else st.warning)(message)
    st.caption("本地验证核对受限算术工具和录制 JSON 快照；其他工具记录会隔离保存。")
    st.info("轨迹处理：重放核对 → 保守剪枝 → 分开保存合格与失败轨迹")
    st.caption("只剪除已核对、相邻且完全相同、后文不引用的调用与返回；原始轨迹保留在质量记录中。")
    st.caption("失败轨迹保留原因和执行证据，不混入合格训练样本。")
