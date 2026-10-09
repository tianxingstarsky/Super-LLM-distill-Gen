"""Capacity and node bindings for a bounded, resumable workflow."""
from __future__ import annotations

from copy import deepcopy
from lib.domain.workflow_package_review import validate_package_review

MAX_CANDIDATES = 100_000
MAX_CONCURRENCY = 16
MAX_BATCH_SIZE = 500
PLAN_BATCH_SIZE = 50
DEFAULT_CONTEXT_WINDOW_TOKENS = 131_072
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
MAX_CONTEXT_WINDOW_TOKENS = 4_000_000
NODE_ROLES = {
    "director": ("generation",),
    "ingest": ("generation", "vision"), "cpt": ("generation", "jev"),
    "sft": ("generation", "jev"), "multiturn": ("generation", "jev"),
    "preference": ("generation", "jev"), "cot": ("generation", "jev"),
    "trim": ("generation", "jev"),
    "package": ("jev",),
    "jev": ("jev",),
}


def node_roles(stage: str, source_mode: str, *, node_generation: dict | None = None,
               package_review: dict | None = None, cpt_processing: dict | None = None) -> tuple[str, ...]:
    if stage in {"package", "jev"}:
        review = validate_package_review(package_review)
        review_node = review.get("node", "package")
        return ("jev",) if review["enabled"] and stage == review_node else ()
    if stage == "ingest":
        return (("vision",) if source_mode == "多模态文档" else
                ("generation",) if source_mode in {"开放需求", "模型辅助文档"} else ())
    if stage == "cpt" and source_mode != "开放需求":
        from lib.domain.cpt_processing import validate_cpt_processing
        return (("generation", "jev") if validate_cpt_processing(cpt_processing)["mode"] == "model" else ())
    if stage == "cot":
        # Model drafts also exist while a custom instruction is incomplete.
        # Derive role requirements from the switch; creation validates text.
        if node_generation is not None and not isinstance(node_generation, dict):
            raise ValueError("invalid_node_generation")
        config = (node_generation or {}).get("cot")
        if config is None:
            return ("jev",)
        if not isinstance(config, dict) or type(config.get("enabled", True)) is not bool:
            raise ValueError("invalid_node_generation")
        if not config.get("enabled", True):
            return ("jev",)
    return NODE_ROLES.get(stage, ())


def validate_node_models(value: dict | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("invalid_node_models")
    result = {}
    for stage, roles in value.items():
        if stage not in NODE_ROLES or not isinstance(roles, dict):
            raise ValueError("invalid_node_models")
        result[stage] = {}
        for role, binding in roles.items():
            if role not in NODE_ROLES[stage] or not isinstance(binding, dict):
                raise ValueError("invalid_node_models")
            if not {"backend", "model"} <= set(binding) or set(binding) - {
                    "backend", "model", "context_window_tokens", "max_output_tokens"}:
                raise ValueError("invalid_node_models")
            for item in (binding["backend"], binding["model"]):
                if not isinstance(item, str) or not item.strip() or len(item) > 200 or any(ord(c) < 32 for c in item):
                    raise ValueError("invalid_node_models")
            context = binding.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
            output = binding.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
            if (type(context) is not int or type(output) is not int or context <= 0 or output <= 0
                    or context > MAX_CONTEXT_WINDOW_TOKENS or output >= context):
                raise ValueError("invalid_node_model_token_limits")
            result[stage][role] = {"backend": binding["backend"].strip(), "model": binding["model"].strip(),
                                   "context_window_tokens": context, "max_output_tokens": output}
    return result


def generation_variant(original: dict, index: int, document_count: int, *, policy_version: int = 2) -> dict:
    """Make one candidate variant while preserving the original source."""
    from lib.domain.workflow_quality import canonical
    import hashlib
    focuses = ("概念解释", "操作步骤", "条件与边界", "故障诊断", "对比判断", "应用场景")
    unit = deepcopy(original)
    unit['id'] = hashlib.sha256(canonical([original['id'], 'variant', index]).encode()).hexdigest()
    if policy_version == 1:
        # Recipe versions through 10 pin item checkpoints to the complete unit
        # hash. Preserve their exact metadata when resuming old paid work.
        unit['generation_variant'] = {'index': index // document_count + 1,
                                      'focus': focuses[index % len(focuses)],
                                      'instruction': '依据同一来源生成不同问题；不要重复已有问法或编造新事实。'}
        return unit
    if policy_version != 2:
        raise ValueError("invalid_generation_variant_policy")
    variant_index = index // document_count
    unit['generation_variant'] = {'index': variant_index + 1,
                                  'source_unit_id': original['id'],
                                  'focus': focuses[variant_index % len(focuses)],
                                  'instruction': '依据同一来源生成不同问题；不要重复已有问法或编造新事实。'}
    return unit


def generation_units(units: list[dict], count: int | None, *, policy_version: int = 2) -> list[dict]:
    """Expand document candidates only. Recorded conversations stay intact."""
    if count is None or not units:
        return units
    result = units[:count]
    documents = [unit for unit in units if unit.get("kind") == "document"]
    if not documents:
        return result
    for index in range(len(result), count):
        original = documents[(index - len(units)) % len(documents)]
        result.append(generation_variant(original, index, len(documents), policy_version=policy_version))
    return result
