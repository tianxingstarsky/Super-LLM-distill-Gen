"""Capacity and node bindings for a bounded, resumable workflow."""
from __future__ import annotations

from copy import deepcopy

MAX_CANDIDATES = 100_000
MAX_CONCURRENCY = 16
MAX_BATCH_SIZE = 500
PLAN_BATCH_SIZE = 50
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
            if set(binding) != {"backend", "model"}:
                raise ValueError("invalid_node_models")
            for item in binding.values():
                if not isinstance(item, str) or not item.strip() or len(item) > 200 or any(ord(c) < 32 for c in item):
                    raise ValueError("invalid_node_models")
            result[stage][role] = {key: item.strip() for key, item in binding.items()}
    return result


def generation_units(units: list[dict], count: int | None) -> list[dict]:
    """Expand document candidates only. Recorded conversations stay intact."""
    if count is None or not units:
        return units
    result = units[:count]
    documents = [unit for unit in units if unit.get("kind") == "document"]
    if not documents:
        return result
    from lib.domain.workflow_quality import canonical
    import hashlib
    focuses = ("概念解释", "操作步骤", "条件与边界", "故障诊断", "对比判断", "应用场景")
    for index in range(len(result), count):
        original = documents[(index - len(units)) % len(documents)]
        variant = index // len(documents) + 1
        unit = deepcopy(original)
        unit["id"] = hashlib.sha256(canonical([original["id"], "variant", index]).encode()).hexdigest()
        unit["generation_variant"] = {"index": variant, "focus": focuses[index % len(focuses)],
                                      "instruction": "依据同一来源生成不同问题；不要重复已有问法或编造新事实。"}
        result.append(unit)
    return result
