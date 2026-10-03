"""Capacity and node bindings for a bounded, resumable workflow."""
from __future__ import annotations

from copy import deepcopy

MAX_CANDIDATES = 100_000
MAX_CONCURRENCY = 16
MAX_BATCH_SIZE = 500
PLAN_BATCH_SIZE = 50
DEFAULT_CONTEXT_WINDOW_TOKENS = 131_072
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
MAX_CONTEXT_WINDOW_TOKENS = 4_000_000
NODE_ROLES = {
    "ingest": ("generation",), "cpt": ("generation", "jev"),
    "sft": ("generation", "jev"), "multiturn": ("generation", "jev"),
    "preference": ("generation", "jev"), "cot": ("jev",),
}


def node_roles(stage: str, source_mode: str) -> tuple[str, ...]:
    if stage in {"ingest", "cpt"} and source_mode != "开放需求":
        return ()
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


def generation_variant(original: dict, index: int, document_count: int) -> dict:
    """Make one candidate variant while preserving the original source."""
    from lib.domain.workflow_quality import canonical
    import hashlib
    focuses = ("概念解释", "操作步骤", "条件与边界", "故障诊断", "对比判断", "应用场景")
    unit = deepcopy(original)
    unit['id'] = hashlib.sha256(canonical([original['id'], 'variant', index]).encode()).hexdigest()
    unit['generation_variant'] = {'index': index // document_count + 1,
                                  'focus': focuses[index % len(focuses)],
                                  'instruction': '依据同一来源生成不同问题；不要重复已有问法或编造新事实。'}
    return unit


def generation_units(units: list[dict], count: int | None) -> list[dict]:
    """Expand document candidates only. Recorded conversations stay intact."""
    if count is None or not units:
        return units
    result = units[:count]
    documents = [unit for unit in units if unit.get("kind") == "document"]
    if not documents:
        return result
    for index in range(len(result), count):
        original = documents[(index - len(units)) % len(documents)]
        result.append(generation_variant(original, index, len(documents)))
    return result
