"""Explicit document-reading modes; model names never imply image support."""
from lib.domain.workflow_scale import validate_node_models
import hashlib
import json


def vision_connection_signature(endpoint: dict) -> str:
    value = {"base_url": endpoint.get("base_url"), "api_format": endpoint.get("api_format", "chat"),
             "models": sorted(endpoint.get("models") or [])}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def supports_vision(endpoint: dict, model: str) -> bool:
    capabilities = endpoint.get("model_capabilities") or {}
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get(model), dict):
        return False
    declaration = capabilities[model]
    return declaration.get("vision") is True and (
        "connection_sha256" not in declaration
        or declaration["connection_sha256"] == vision_connection_signature(endpoint))


def validate_document_parser(value=None) -> dict:
    if value is None:
        return {"mode": "native"}
    if isinstance(value, dict) and value.get("unconfirmed"):
        raise ValueError("document_model_vision_not_confirmed")
    if not isinstance(value, dict) or set(value) - {"mode", "binding"}:
        raise ValueError("invalid_document_parser")
    if value.get("mode") == "native":
        return {"mode": "native"}
    if value.get("mode") == "model":
        if not isinstance(value.get("binding"), dict):
            raise ValueError("document_text_model_required")
        binding = validate_node_models({"ingest": {"generation": value["binding"]}})["ingest"]["generation"]
        return {"mode": "model", "binding": binding}
    if value.get("mode") != "vision" or not isinstance(value.get("binding"), dict):
        raise ValueError("document_vision_model_required")
    binding = validate_node_models({"ingest": {"vision": value["binding"]}})["ingest"]["vision"]
    return {"mode": "vision", "binding": binding}
