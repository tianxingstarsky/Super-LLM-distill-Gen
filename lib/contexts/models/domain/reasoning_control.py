"""Platform effort choices with a frozen, separately inspectable provider map."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


STANDARD_REASONING_SCHEMA = "shujian.reasoning-effort/v1"
STANDARD_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def validate_standard_effort(effort):
    if not isinstance(effort, str) or effort not in STANDARD_REASONING_EFFORTS:
        raise ValueError("invalid_standard_reasoning_effort")
    return effort


def validate_standard_reasoning(value, *, max_output_tokens=None):
    from .reasoning import validate_reasoning

    if (set(value) != {"schema", "mode", "effort", "provider_mapping"}
            or value.get("schema") != STANDARD_REASONING_SCHEMA or value.get("mode") != "enabled"):
        raise ValueError("invalid_standard_reasoning")
    validate_standard_effort(value["effort"])
    mapping = value["provider_mapping"]
    if not isinstance(mapping, dict) or set(mapping) != set(STANDARD_REASONING_EFFORTS):
        raise ValueError("invalid_reasoning_provider_mapping")
    normalized = {}
    for effort, native in mapping.items():
        if not isinstance(native, dict) or "schema" in native or native.get("mode") not in {"enabled", "disabled"}:
            raise ValueError("invalid_reasoning_provider_mapping")
        normalized[effort] = validate_reasoning(native, max_output_tokens=max_output_tokens)
    return {"schema": STANDARD_REASONING_SCHEMA, "mode": "enabled", "effort": value["effort"],
            "provider_mapping": normalized}


def standard_reasoning(effort, *, model_info, api_format="chat", max_output_tokens=None,
                       provider_mapping=None):
    """Freeze a platform choice; capability refreshes cannot relabel saved runs.

    Known named levels use the next supported level at or above the platform
    position, capped at the provider's highest level. Unknown vendor vocabularies
    require an explicit map. These are product defaults, not vendor equivalences.
    """
    validate_standard_effort(effort)
    mapping = deepcopy(provider_mapping)
    if mapping is None:
        info = model_info or {}
        scheme = info.get("reasoning_format")
        levels = info.get("reasoning_efforts") or []
        if api_format == "anthropic" and scheme == "anthropic_budget":
            if type(max_output_tokens) is not int or max_output_tokens <= 1024:
                raise ValueError("model_reasoning_budget_exceeds_output")
            mapping = {level: {"mode": "enabled", "budget_tokens": min(budget, max_output_tokens - 1)}
                       for level, budget in zip(STANDARD_REASONING_EFFORTS, (1024, 2048, 4096, 8192, 16384))}
        elif levels:
            if any(level not in STANDARD_REASONING_EFFORTS for level in levels):
                raise ValueError("reasoning_provider_mapping_required")
            available = [level for level in STANDARD_REASONING_EFFORTS if level in levels]
            mapping = {}
            for index, level in enumerate(STANDARD_REASONING_EFFORTS):
                native = next((item for item in STANDARD_REASONING_EFFORTS[index:] if item in available), available[-1])
                mapping[level] = {"mode": "enabled", "effort": native}
        elif ((scheme == "deepseek" and api_format in {"chat", "anthropic"})
              or (scheme == "anthropic_adaptive" and api_format == "anthropic")):
            mapping = {level: {"mode": "enabled"} for level in STANDARD_REASONING_EFFORTS}
        else:
            raise ValueError("reasoning_provider_mapping_required")
    return validate_standard_reasoning({"schema": STANDARD_REASONING_SCHEMA, "mode": "enabled",
        "effort": effort, "provider_mapping": mapping}, max_output_tokens=max_output_tokens)


def resolve_provider_reasoning(value, *, max_output_tokens=None):
    """Translate only at dispatch; legacy native choices retain their meaning."""
    from .reasoning import validate_reasoning

    choice = validate_reasoning(value, max_output_tokens=max_output_tokens)
    if choice.get("schema") != STANDARD_REASONING_SCHEMA:
        return choice
    return deepcopy(choice["provider_mapping"][choice["effort"]])


def reasoning_control_receipt(value):
    """Keep platform intent distinct from the effective provider selection."""
    from .reasoning import validate_reasoning

    choice = validate_reasoning(value)
    if choice.get("schema") != STANDARD_REASONING_SCHEMA:
        return None
    encoded = json.dumps(choice["provider_mapping"], sort_keys=True, separators=(",", ":"))
    return {"schema": STANDARD_REASONING_SCHEMA, "effort": choice["effort"],
            "provider": deepcopy(choice["provider_mapping"][choice["effort"]]),
            "mapping_sha256": hashlib.sha256(encoded.encode()).hexdigest()}
