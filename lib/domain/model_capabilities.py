"""Normalize declared model capacities without treating names as evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


CAPACITY_FIELDS = ("context_window_tokens", "max_output_tokens")
FEATURE_FIELDS = ("vision", "pdf", "tools")
INFO_FIELDS = CAPACITY_FIELDS + FEATURE_FIELDS


def empty_model_info(model: str) -> dict:
    return {"model": model, **{field: None for field in INFO_FIELDS}, "sources": {}}


def _positive(*values):
    return next((value for value in values if type(value) is int and 0 < value <= 4_000_000), None)


def _boolean(*values):
    return next((value for value in values if type(value) is bool), None)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def normalize_model_info(model: str, value: dict, source: str) -> dict:
    """Read common SDK, Anthropic, OpenRouter and models.dev metadata fields."""
    value = _mapping(value)
    limit = _mapping(value.get("limit"))
    top = _mapping(value.get("top_provider"))
    capabilities = _mapping(value.get("capabilities"))
    architecture = _mapping(value.get("architecture"))
    modalities = _mapping(value.get("modalities"))
    inputs = next((item for item in (architecture.get("input_modalities"), modalities.get("input"),
                                    value.get("input_modalities"), value.get("input"))
                   if isinstance(item, list) and all(isinstance(part, str) for part in item)), None)
    parameters = value.get("supported_parameters")
    result = empty_model_info(model)
    result["context_window_tokens"] = _positive(
        value.get("context_window_tokens"), value.get("context_window"), value.get("context_length"),
        value.get("contextWindow"), value.get("max_input_tokens"), limit.get("context"))
    result["max_output_tokens"] = _positive(
        value.get("max_output_tokens"), value.get("maxOutputTokens"), value.get("max_tokens"),
        value.get("maxTokens"), limit.get("output"), top.get("max_completion_tokens"))
    result["vision"] = _boolean(
        value.get("vision"), value.get("supports_vision"),
        _mapping(capabilities.get("image_input")).get("supported"),
        "image" in inputs if inputs is not None else None)
    # A generic attachment flag does not establish native PDF input support.
    result["pdf"] = _boolean(
        value.get("pdf"), value.get("supports_pdf_input"),
        _mapping(capabilities.get("pdf_input")).get("supported"),
        "pdf" in inputs if inputs is not None and "pdf" in inputs else None)
    result["tools"] = _boolean(
        value.get("tools"), value.get("tool_call"), value.get("supports_tool_calls"),
        value.get("supports_function_calling"),
        _mapping(capabilities.get("tool_calling")).get("supported"),
        _mapping(capabilities.get("function_calling")).get("supported"),
        "tools" in parameters if isinstance(parameters, list) else None)
    result["sources"] = {field: source for field in INFO_FIELDS if result[field] is not None}
    name = value.get("display_name") or value.get("name")
    if isinstance(name, str) and len(name) <= 300:
        result["name"] = name
    return result


def merge_model_info(model: str, *records: dict) -> dict:
    """Earlier records win; unknown values never replace a known declaration."""
    result = empty_model_info(model)
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in INFO_FIELDS:
            value = record.get(field)
            valid = (type(value) is int and 0 < value <= 4_000_000 if field in CAPACITY_FIELDS
                     else type(value) is bool)
            if result[field] is None and valid:
                result[field] = value
                result["sources"][field] = (record.get("sources") or {}).get(field, "manual")
        for field in ("provider", "name", "checked_at", "catalog_source", "catalog_status"):
            if field not in result and record.get(field) is not None:
                result[field] = deepcopy(record[field])
    return result


def validate_manual_capabilities(value: dict) -> dict:
    if set(value) - set(INFO_FIELDS):
        raise ValueError("invalid_model_capability")
    for field, item in value.items():
        if item is None:
            continue
        if field in CAPACITY_FIELDS:
            if type(item) is not int or not 0 < item <= 4_000_000:
                raise ValueError("invalid_model_capability")
        elif type(item) is not bool:
            raise ValueError("invalid_model_capability")
    context, output = value.get("context_window_tokens"), value.get("max_output_tokens")
    if context is not None and output is not None and output >= context:
        raise ValueError("invalid_node_model_token_limits")
    return deepcopy(value)


def connection_signature(endpoint: dict, credential: str, model: str = "") -> str:
    """Bind evidence to route and credentials without exposing their values."""
    payload = {"base_url": endpoint.get("base_url"), "api_format": endpoint.get("api_format", "chat"),
               "model": model, "credential_sha256": hashlib.sha256(credential.encode()).hexdigest()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
