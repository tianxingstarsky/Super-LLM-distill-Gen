"""Explicit document-reading modes; model names never imply image support."""
from lib.domain.workflow_scale import validate_node_models
import hashlib
import json
from pathlib import Path
import re


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
    if isinstance(value, dict) and value.get("mode") == "docling":
        allowed = {"mode", "artifacts_path", "ocr", "ocr_models_path", "ocr_languages",
                   "formula_enrichment", "runtime_python", "inline_runtime_python", "assistance"}
        if (set(value) - allowed or type(value.get("ocr", False)) is not bool
                or type(value.get("formula_enrichment", False)) is not bool):
            raise ValueError("invalid_document_parser")
        def local_path(name):
            path = value.get(name)
            if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
                raise ValueError("document_docling_local_models_required")
            return str(Path(path))
        result = {"mode": "docling", "artifacts_path": local_path("artifacts_path"),
                  "ocr": value.get("ocr", False), "formula_enrichment": value.get("formula_enrichment", False)}
        if value.get("runtime_python"):
            result["runtime_python"] = local_path("runtime_python")
        elif "runtime_python" in value and value["runtime_python"] not in (None, ""):
            raise ValueError("invalid_document_parser")
        if value.get("inline_runtime_python"):
            if not result.get("runtime_python") or not result["formula_enrichment"]:
                raise ValueError("document_docling_inline_runtime_requires_formula")
            result["inline_runtime_python"] = local_path("inline_runtime_python")
        elif "inline_runtime_python" in value and value["inline_runtime_python"] not in (None, ""):
            raise ValueError("invalid_document_parser")
        if result["ocr"]:
            languages = value.get("ocr_languages", ["en"])
            if (not isinstance(languages, list) or not 1 <= len(languages) <= 8
                    or any(not isinstance(lang, str) or not re.fullmatch(r"[a-z]{2,3}(?:_[a-z]{2,4})?", lang)
                           for lang in languages) or len(set(languages)) != len(languages)):
                raise ValueError("invalid_document_docling_ocr_languages")
            result.update(ocr_models_path=local_path("ocr_models_path"), ocr_languages=list(languages))
        assistance=value.get("assistance")
        if assistance is not None:
            if (not isinstance(assistance,dict) or set(assistance)-{"enabled","binding","max_rounds","review_scope"}
                    or type(assistance.get("enabled")) is not bool):raise ValueError("invalid_document_parser")
            if assistance["enabled"]:
                if not isinstance(assistance.get("binding"),dict):raise ValueError("document_vision_model_required")
                rounds=assistance.get("max_rounds",2)
                if type(rounds) is not int or not 1<=rounds<=2:raise ValueError("invalid_vision_assist_rounds")
                binding=validate_node_models({"ingest":{"vision":assistance.get("binding")}})["ingest"]["vision"]
                result["assistance"]={"enabled":True,"binding":binding,"max_rounds":rounds}
                scope=assistance.get("review_scope","off")
                if not isinstance(scope,str) or scope not in {"off","regions","pages"}:raise ValueError("invalid_vision_review_scope")
                if scope!="off":result["assistance"]["review_scope"]=scope
        return result
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
