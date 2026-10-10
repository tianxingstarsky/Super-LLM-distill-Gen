"""Pure rules for node-scoped model drafts and validated run snapshots."""
from copy import deepcopy

from lib.domain.workflow_scale import (DEFAULT_CONTEXT_WINDOW_TOKENS,
                                       DEFAULT_MAX_OUTPUT_TOKENS,
                                       MAX_CONTEXT_WINDOW_TOKENS, node_roles,
                                       validate_node_models)


def initialize_draft(nodes, source_mode, draft, initialized, inventory, *, node_generation=None,
                     package_review=None, cpt_processing=None, review_repair=None):
    draft = validate_node_models(draft)
    if (package_review or {}).get("node") == "jev" and "jev" not in draft and draft.get("package", {}).get("jev"):
        # Preserve the chosen terminal reviewer when opening an older draft in
        # the new node layout. Explicit JEV selections always take precedence.
        draft["jev"] = {"jev": deepcopy(draft["package"]["jev"])}
    initialized = set(initialized)
    if "review" in nodes and "review" not in draft:
        # Reuse durable choices when an older workflow draft gains its review
        # node. The two roles remain independent after this one-time migration.
        review_roles = node_roles("review", source_mode, package_review=package_review,
                                  review_repair=review_repair)
        migrated = {}
        for role in review_roles:
            candidates = (("jev", "package", "sft", "cot", "preference", "multiturn")
                          if role == "jev" else ("sft", "cot", "multiturn", "preference", "cpt"))
            for source in candidates:
                if draft.get(source, {}).get(role):
                    migrated[role] = deepcopy(draft[source][role])
                    break
        if migrated:
            draft["review"] = migrated
    endpoints = {row["name"]: row for row in inventory.get("backends", [])}
    for node in nodes:
        for role in node_roles(node, source_mode, node_generation=node_generation,
                               package_review=package_review, cpt_processing=cpt_processing, review_repair=review_repair):
            marker = node + ":" + role
            if marker in initialized or role in draft.get(node, {}):
                initialized.add(marker)
                continue
            slot = (inventory.get("roles") or {}).get(role) or {}
            backend = slot.get("backend") or inventory.get("default_backend")
            endpoint = endpoints.get(backend)
            if endpoint is None:
                continue
            listed_models = list(dict.fromkeys([
                *(endpoint.get("models") or []), *(endpoint.get("discovered_models") or []),
            ]))
            suggested_model = slot.get("model") or (
                inventory.get("default_model") if backend == inventory.get("default_backend") else "")
            # Role defaults are suggestions, not proof that a model is usable.
            # An unlisted custom name must be entered explicitly on the node.
            if suggested_model and suggested_model not in listed_models:
                continue
            model = suggested_model or next(iter(listed_models), "")
            if model:
                info = (endpoint.get("model_info") or {}).get(model) or {}
                context = info.get("context_window_tokens")
                output = info.get("max_output_tokens")
                if type(context) is not int or not 1 < context <= MAX_CONTEXT_WINDOW_TOKENS:
                    context = DEFAULT_CONTEXT_WINDOW_TOKENS
                if type(output) is not int or output <= 0:
                    output = DEFAULT_MAX_OUTPUT_TOKENS
                output = min(output, DEFAULT_MAX_OUTPUT_TOKENS, context - 1)
                draft.setdefault(node, {})[role] = {
                    "backend": backend, "model": model,
                    "context_window_tokens": context, "max_output_tokens": output,
                }
                initialized.add(marker)
    return validate_node_models(draft), sorted(initialized), endpoints


def missing_bindings(nodes, source_mode, bindings, endpoints, *, node_generation=None,
                     package_review=None, cpt_processing=None, review_repair=None):
    validated = validate_node_models(bindings)
    return [(node, role) for node in nodes for role in node_roles(
                node, source_mode, node_generation=node_generation, package_review=package_review,
                cpt_processing=cpt_processing, review_repair=review_repair)
            if not validated.get(node, {}).get(role) or validated[node][role]["backend"] not in endpoints]


def binding_snapshot(nodes, source_mode, bindings, endpoints, *, node_generation=None,
                     package_review=None, cpt_processing=None, review_repair=None):
    if missing_bindings(nodes, source_mode, bindings, endpoints, node_generation=node_generation,
                        package_review=package_review, cpt_processing=cpt_processing, review_repair=review_repair):
        raise ValueError("请为所有需要模型的节点选择服务与模型。")
    validated = validate_node_models(bindings)
    return {node: {role: deepcopy(validated[node][role])
                   for role in node_roles(node, source_mode, node_generation=node_generation,
                                          package_review=package_review, cpt_processing=cpt_processing, review_repair=review_repair)}
            for node in nodes if node_roles(node, source_mode, node_generation=node_generation,
                                            package_review=package_review, cpt_processing=cpt_processing, review_repair=review_repair)}
