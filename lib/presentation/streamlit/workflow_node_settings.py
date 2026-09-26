"""Model choices belong to nodes; endpoint credentials remain in service settings."""
from copy import deepcopy

import streamlit as st

from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.domain.workflow_scale import node_roles


def node_bindings(application: WorkflowNodeModelsApplication, nodes, source_mode, workspace):
    draft_key = f"workflow-node-bindings:{workspace}"
    draft = deepcopy(st.session_state.get(draft_key, {}))
    initialized_key = draft_key + ":initialized"
    draft, initialized, endpoints = application.prepare_draft(
        nodes, source_mode, draft, st.session_state.get(initialized_key, ()))
    st.session_state[draft_key] = draft
    st.session_state[initialized_key] = sorted(initialized)
    return draft, endpoints


def render_node_models(node, source_mode, workspace, bindings, endpoints):
    previous = deepcopy(bindings)
    roles = node_roles(node, source_mode)
    if not roles:
        explanation = {
            "ingest": "解析上传来源并保留来源位置；此步骤不调用生成模型。",
            "cpt": "清洗、分块并去重已有语料；此步骤不调用生成模型。",
            "agent": "核对已记录的工具轨迹；此节点不调用模型。验证环境在下方选择。",
            "gsm8k": "生成本地整数算术题并逐步验算；不是通用数学题生成器，此节点不调用模型。",
            "package": "核对产物清单并整理候选数据；人工审核和正式发布在后续完成，此节点不调用模型。",
        }.get(node, "此节点不需要配置模型。")
        st.info(explanation)
        return
    if not endpoints:
        st.warning("请先在模型服务中登记服务地址与凭据，再回到节点选择模型。")
        return
    for role in roles:
        st.html('<p style="font-size:14px;margin:14px 0 8px"><strong>'
                + ("生成模型" if role == "generation" else "独立质量评审模型") + '</strong></p>')
        binding = bindings.get(node, {}).get(role, {})
        prefix = f"node-model:{workspace}:{node}:{role}"
        names = list(endpoints)
        if binding.get("backend") and binding["backend"] not in endpoints:
            st.warning("原模型服务已不可用，请为此节点重新选择。")
        if prefix + ":backend" in st.session_state and st.session_state[prefix + ":backend"] not in names:
            st.session_state[prefix + ":backend"] = None
        backend = st.selectbox("模型服务", names, index=names.index(binding["backend"])
                               if binding.get("backend") in names else None, key=prefix + ":backend",
                               placeholder="选择模型服务")
        if backend is None:
            if binding.get("backend") in endpoints:
                bindings.setdefault(node, {}).pop(role, None)
            continue
        models = list(endpoints[backend].get("models") or [])
        if binding.get("backend") == backend and binding.get("model") not in models and binding.get("model"):
            models.append(binding["model"])
        model = st.selectbox("模型", models, index=models.index(binding["model"])
                             if binding.get("model") in models else 0 if models else None,
                             accept_new_options=True, key=prefix + ":model:" + backend,
                             placeholder="选择或输入模型名")
        if model:
            bindings.setdefault(node, {})[role] = {"backend": backend, "model": model}
        else:
            bindings.setdefault(node, {}).pop(role, None)
    st.session_state[f"workflow-node-bindings:{workspace}"] = deepcopy(bindings)
    st.caption("每个节点独立保存选择；开始运行后，本次配置固定。")
    if bindings != previous:
        st.rerun()


def snapshot_available_bindings(nodes, source_mode, bindings, endpoints):
    return {node: {role: bindings[node][role] for role in node_roles(node, source_mode)
                   if role in bindings.get(node, {}) and bindings[node][role]["backend"] in endpoints} for node in nodes}


def render_agent_verification(workspace, capabilities):
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
    st.caption("本地验证核对受限算术工具和录制 JSON 快照；其他工具记录会隔离保存。")
    st.info("轨迹处理：重放核对 → 保守剪枝 → 分开保存合格与失败轨迹")
    st.caption("只剪除已核对、相邻且完全相同、后文不引用的调用与返回；原始轨迹保留在质量记录中。")
    st.caption("失败轨迹保留原因和执行证据，不混入合格训练样本。")
