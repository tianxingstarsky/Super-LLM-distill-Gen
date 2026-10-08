"""Configure each node's models and service connection in the node panel."""
from copy import deepcopy

import streamlit as st

from lib.application.backend_service import BackendApplication
from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.domain.workflow_scale import (DEFAULT_CONTEXT_WINDOW_TOKENS,
                                       DEFAULT_MAX_OUTPUT_TOKENS,
                                       MAX_CONTEXT_WINDOW_TOKENS, node_roles)
from lib.model_protocols import API_FORMATS


_API_FORMAT_LABELS = {"chat": "Chat Completions", "responses": "OpenAI Responses",
                      "anthropic": "Anthropic Messages"}


def node_bindings(application: WorkflowNodeModelsApplication, nodes, source_mode, workspace):
    draft_key = f"workflow-node-bindings:{workspace}"
    draft = deepcopy(st.session_state.get(draft_key, {}))
    initialized_key = draft_key + ":initialized"
    draft, initialized, endpoints = application.prepare_draft(
        nodes, source_mode, draft, st.session_state.get(initialized_key, ()))
    st.session_state[draft_key] = draft
    st.session_state[initialized_key] = sorted(initialized)
    return draft, endpoints


def _reusable_bindings(node: str, role: str, bindings: dict, endpoints: dict) -> list[tuple[str, dict]]:
    """Offer only usable models already chosen for the same role on another node."""
    seen = set()
    choices = []
    for source, roles in bindings.items():
        if source == node:
            continue
        binding = roles.get(role, {})
        backend = binding.get("backend")
        if backend not in endpoints or not binding.get("model"):
            continue
        signature = (backend, binding["model"], binding["context_window_tokens"],
                     binding["max_output_tokens"])
        if signature in seen:
            continue
        seen.add(signature)
        choices.append((source, binding))
    return choices


def _reuse_binding(workspace: str, node: str, role: str, binding: dict) -> None:
    """Copy an explicit choice into one node without changing the source node."""
    draft_key = f"workflow-node-bindings:{workspace}"
    draft = deepcopy(st.session_state.get(draft_key, {}))
    copied = deepcopy(binding)
    draft.setdefault(node, {})[role] = copied
    st.session_state[draft_key] = draft
    prefix = f"node-model:{workspace}:{node}:{role}"
    backend, model = copied["backend"], copied["model"]
    st.session_state[prefix + ":backend"] = backend
    st.session_state[prefix + ":model:" + backend] = model
    st.session_state[prefix + ":context:" + backend + ":" + model] = copied["context_window_tokens"]
    st.session_state[prefix + ":output:" + backend + ":" + model] = copied["max_output_tokens"]


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
        with st.form(f"{form_prefix}:{epoch}", clear_on_submit=True):
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
        if not submit:
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
            bindings.setdefault(node, {})[target_role] = {
                "backend": name.strip(), "model": model.strip(),
                "context_window_tokens": DEFAULT_CONTEXT_WINDOW_TOKENS,
                "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            }
            st.session_state[f"workflow-node-bindings:{workspace}"] = deepcopy(bindings)
            st.session_state[f"workflow-node-pending-binding:{workspace}:{node}"] = {
                "role": target_role, "backend": name.strip(), "model": model.strip(),
            }
        st.session_state[form_prefix + ":clear-secret"] = secret_key
        st.session_state[form_prefix + ":epoch"] = epoch + 1
        st.toast("连接已保存，并已用于当前节点。" if target_role else "连接已保存；可在当前节点选择使用。")
        st.rerun()


def render_node_models(node, source_mode, workspace, bindings, endpoints, *,
                       backend_application: BackendApplication | None = None):
    pending = st.session_state.pop(f"workflow-node-pending-binding:{workspace}:{node}", None)
    if pending and pending.get("role") in node_roles(node, source_mode):
        prefix = f"node-model:{workspace}:{node}:{pending['role']}"
        st.session_state[prefix + ":backend"] = pending["backend"]
        st.session_state[prefix + ":model:" + pending["backend"]] = pending["model"]
    previous = deepcopy(bindings)
    roles = node_roles(node, source_mode)
    if not roles:
        explanation = {
            "ingest": "解析上传来源并保留来源位置；此步骤不调用生成模型。",
            "cpt": "清洗、分块并去重已有语料；此步骤不调用生成模型。",
            "agent": "核对已记录的工具轨迹；此节点不调用模型。验证环境在下方选择。",
            "gsm8k": "生成可复现的多步整数算术题，核对计算标注和最终答案；不处理通用数学证明，此节点不调用模型。",
            "package": "核对产物清单并整理候选数据；人工审核和正式发布在后续完成，此节点不调用模型。",
        }.get(node, "此节点不需要配置模型。")
        st.info(explanation)
        return
    if not endpoints:
        st.info("当前没有可用的模型连接。请在下方登记服务地址和模型。")
        _connect_service(node, workspace, roles, bindings, endpoints, backend_application)
        return
    for role in roles:
        st.html('<p style="font-size:14px;margin:14px 0 8px"><strong>'
                + ("多模态识别模型" if role == "vision" else "生成模型" if role == "generation" else "独立质量评审模型") + '</strong></p>')
        binding = bindings.get(node, {}).get(role, {})
        prefix = f"node-model:{workspace}:{node}:{role}"
        if not binding:
            for source, reusable in _reusable_bindings(node, role, bindings, endpoints):
                model_name = reusable["model"]
                label = f"沿用 {source.upper()} 节点的 {model_name}"
                st.button(label, key=prefix + ":reuse:" + source,
                          on_click=_reuse_binding, args=(workspace, node, role, reusable),
                          help="只复制到当前节点。模型服务、模型和 token 上限可继续分别调整。",
                          use_container_width=True)
        names = list(endpoints)
        if binding.get("backend") and binding["backend"] not in endpoints:
            st.warning("原模型服务已不可用，请为此节点重新选择。")
        if prefix + ":backend" in st.session_state and st.session_state[prefix + ":backend"] not in names:
            st.session_state[prefix + ":backend"] = None
        backend = st.selectbox("模型服务", names, index=names.index(binding["backend"])
                               if binding.get("backend") in names else None, key=prefix + ":backend",
                               placeholder="选择模型服务",
                               format_func=lambda name: name + " · " + _API_FORMAT_LABELS.get(
                                   endpoints[name].get("api_format", "chat"), "Chat Completions"))
        if backend is None:
            if binding.get("backend") in endpoints:
                bindings.setdefault(node, {}).pop(role, None)
            continue
        models = list(endpoints[backend].get("models") or [])
        if binding.get("backend") == backend and binding.get("model") not in models and binding.get("model"):
            models.append(binding["model"])
        model = st.selectbox("模型", models, index=models.index(binding["model"])
                             if binding.get("backend") == backend and binding.get("model") in models else None,
                             accept_new_options=True, key=prefix + ":model:" + backend,
                             placeholder="选择或输入模型名")
        if model:
            same_model = binding.get("backend") == backend and binding.get("model") == model
            context_default = (binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
                               if same_model else DEFAULT_CONTEXT_WINDOW_TOKENS)
            output_default = (binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
                              if same_model else DEFAULT_MAX_OUTPUT_TOKENS)
            context_column, output_column = st.columns(2, gap="small")
            with context_column:
                context_tokens = st.number_input(
                    "上下文窗口（tokens）", min_value=1, max_value=MAX_CONTEXT_WINDOW_TOKENS,
                    value=int(context_default), step=1024,
                    key=prefix + ":context:" + backend + ":" + model,
                )
            with output_column:
                output_tokens = st.number_input(
                    "单次输出上限（tokens）", min_value=1, max_value=MAX_CONTEXT_WINDOW_TOKENS - 1,
                    value=int(output_default), step=1024,
                    key=prefix + ":output:" + backend + ":" + model,
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
        else:
            bindings.setdefault(node, {}).pop(role, None)
    st.session_state[f"workflow-node-bindings:{workspace}"] = deepcopy(bindings)
    st.caption("每个节点独立保存选择。默认上下文 131,072、单次输出 32,768 tokens；需按模型能力调整。开始运行后，本次配置固定。")
    _connect_service(node, workspace, roles, bindings, endpoints, backend_application)
    if bindings != previous:
        st.rerun()


def snapshot_available_bindings(nodes, source_mode, bindings, endpoints):
    return {node: {role: bindings[node][role] for role in node_roles(node, source_mode)
                   if role in bindings.get(node, {}) and bindings[node][role]["backend"] in endpoints} for node in nodes}


def document_parser_mode(workspace):
    """Keep the chosen parser when Streamlit removes an unrendered widget."""
    draft_key = f"workflow-document-parse-mode-draft:{workspace}"
    mode = st.session_state.get(draft_key, st.session_state.get(
        f"workflow-document-parse-mode:{workspace}", "native"))
    if mode not in {"native", "vision"}:
        mode = "native"
    st.session_state[draft_key] = mode
    return mode


def _save_document_parser_mode(workspace):
    mode = st.session_state.get(f"workflow-document-parse-mode:{workspace}", "native")
    if mode in {"native", "vision"}:
        st.session_state[f"workflow-document-parse-mode-draft:{workspace}"] = mode


def render_document_parser(workspace, bindings, endpoints, *, backend_application=None):
    """Configure document reading on the input node and require explicit vision support."""
    from lib.domain.document_parser import supports_vision, vision_connection_signature
    key = f"workflow-document-parse-mode:{workspace}"
    saved_mode = document_parser_mode(workspace)
    if key not in st.session_state:
        st.session_state[key] = saved_mode
    mode = st.radio("文档解析方式", ("native", "vision"), horizontal=True, key=key,
                    on_change=_save_document_parser_mode, args=(workspace,),
                    format_func=lambda value: "本地文本解析" if value == "native" else "多模态识别")
    if mode == "native":
        st.caption("读取文档文字，不调用模型。扫描 PDF 与图片请使用多模态识别。")
        return {"mode": "native"}
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
    st.caption("图片输入能力：用户已核实并在本机确认。识别结果仍需核对原文。")
    return {"mode": "vision", "binding": deepcopy(binding)}


def render_agent_verification(workspace, capabilities, check_environment=None):
    draft_key = f"workflow-agent-mode:{workspace}"
    draft = st.session_state.get(draft_key, "local")
    mode = st.selectbox("轨迹验证方式", ["local", "isolated"], index=1 if draft == "isolated" else 0,
                        format_func=lambda value: "本地验证" if value == "local" else "隔离验证",
                        key=f"agent-mode-choice:{workspace}")
    st.session_state[draft_key] = mode
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
