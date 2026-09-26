"""Model choices belong to nodes; endpoint credentials remain in service settings."""
from copy import deepcopy

import streamlit as st

from lib import backend_manager as bm
from lib.domain.workflow_scale import node_roles


def node_bindings(nodes, source_mode, workspace):
    inventory = bm.list_backends()
    rows = inventory.get("backends") or []
    endpoints = {row["name"]: row for row in rows}
    draft_key = f"workflow-node-bindings:{workspace}"
    draft = deepcopy(st.session_state.get(draft_key, {}))
    initialized_key = draft_key + ":initialized"
    initialized = set(st.session_state.get(initialized_key, ()))
    for node in nodes:
        for role in node_roles(node, source_mode):
            marker = node + ":" + role
            if marker in initialized or role in draft.get(node, {}):
                initialized.add(marker)
                continue
            initialized.add(marker)
            slot = (inventory.get("roles") or {}).get(role) or {}
            backend = slot.get("backend") or inventory.get("default_backend")
            row = endpoints.get(backend, {})
            model = slot.get("model") or next(iter(row.get("models") or []), "")
            if backend and model:
                draft.setdefault(node, {})[role] = {"backend": backend, "model": model}
    st.session_state[draft_key] = draft
    st.session_state[initialized_key] = sorted(initialized)
    return draft, endpoints


def render_node_models(node, source_mode, workspace, bindings, endpoints):
    previous = deepcopy(bindings)
    roles = node_roles(node, source_mode)
    if not roles:
        st.info("此节点使用本地规则，不需要配置模型。")
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
        backend = st.selectbox("模型服务", names, index=names.index(binding["backend"])
                               if binding.get("backend") in names else 0, key=prefix + ":backend")
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


def snapshot_bindings(nodes, source_mode, bindings, endpoints=None):
    result = {}
    for node in nodes:
        for role in node_roles(node, source_mode):
            binding = bindings.get(node, {}).get(role)
            if not binding or (endpoints is not None and binding["backend"] not in endpoints):
                raise ValueError("请为所有需要模型的节点选择服务与模型。")
            result.setdefault(node, {})[role] = deepcopy(binding)
    return result


def snapshot_available_bindings(nodes, source_mode, bindings):
    return {node: {role: bindings[node][role] for role in node_roles(node, source_mode)
                   if role in bindings.get(node, {})} for node in nodes}
