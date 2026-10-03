"""Pure rules for node-scoped model drafts and validated run snapshots."""
from copy import deepcopy

from lib.domain.workflow_scale import node_roles, validate_node_models


def initialize_draft(nodes, source_mode, draft, initialized, inventory):
    draft = validate_node_models(draft)
    initialized = set(initialized)
    endpoints = {row["name"]: row for row in inventory.get("backends", [])}
    for node in nodes:
        for role in node_roles(node, source_mode):
            marker = node + ":" + role
            if marker in initialized or role in draft.get(node, {}):
                initialized.add(marker)
                continue
            slot = (inventory.get("roles") or {}).get(role) or {}
            backend = slot.get("backend") or inventory.get("default_backend")
            endpoint = endpoints.get(backend)
            if endpoint is None:
                continue
            listed_models = endpoint.get("models") or []
            suggested_model = slot.get("model") or (
                inventory.get("default_model") if backend == inventory.get("default_backend") else "")
            # Role defaults are suggestions, not proof that a model is usable.
            # An unlisted custom name must be entered explicitly on the node.
            if suggested_model and suggested_model not in listed_models:
                continue
            model = suggested_model or next(iter(listed_models), "")
            if model:
                draft.setdefault(node, {})[role] = {"backend": backend, "model": model}
                initialized.add(marker)
    return validate_node_models(draft), sorted(initialized), endpoints


def missing_bindings(nodes, source_mode, bindings, endpoints):
    validated = validate_node_models(bindings)
    return [(node, role) for node in nodes for role in node_roles(node, source_mode)
            if not validated.get(node, {}).get(role) or validated[node][role]["backend"] not in endpoints]


def binding_snapshot(nodes, source_mode, bindings, endpoints):
    if missing_bindings(nodes, source_mode, bindings, endpoints):
        raise ValueError("请为所有需要模型的节点选择服务与模型。")
    validated = validate_node_models(bindings)
    return {node: {role: deepcopy(validated[node][role]) for role in node_roles(node, source_mode)}
            for node in nodes if node_roles(node, source_mode)}
