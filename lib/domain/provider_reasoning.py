"""Keep provider reasoning separate from explanations authored in model JSON."""
from __future__ import annotations

from copy import deepcopy
import re


def select_candidate_reasoning(candidate: dict, *, styled: bool = False) -> dict:
    """Select trusted response metadata without promoting model-authored keys.

    Transport details live on a dict subclass supplied by the client/checkpoint
    boundary. A JSON key named ``response_details`` is ordinary model output and
    grants no provenance. Provider summaries are kept as summaries, never used
    as full reasoning. Native text is retained exactly as the API returned it.
    """
    if styled:
        authored = candidate.get("reasoning")
        if authored is not None and not isinstance(authored, str):
            raise ValueError("invalid_generated_reasoning")
        authored = authored if isinstance(authored, str) and authored.strip() else ""
        return {"content": authored,
                "origin": "prompt_styled_generation" if authored else "none"}

    details = getattr(candidate, "response_details", None)
    details = details if isinstance(details, dict) else {}
    native = details.get("reasoning_content")
    if native is not None and not isinstance(native, str):
        raise ValueError("invalid_provider_reasoning")
    native = native if isinstance(native, str) and native.strip() else ""
    authored = ""
    if not native:
        authored = candidate.get("reasoning")
        if authored is not None and not isinstance(authored, str):
            raise ValueError("invalid_generated_reasoning")
        authored = authored if isinstance(authored, str) and authored.strip() else ""
    selected = {"content": native or authored,
                "origin": ("provider_reasoning" if native else
                           "synthetic_explanation" if authored else "none")}
    summary = details.get("reasoning_summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("invalid_provider_reasoning_summary")
    if summary and summary.strip():
        selected["summary"] = summary
    api_format = details.get("api_format")
    if isinstance(api_format, str) and api_format in {"chat", "responses", "anthropic"}:
        selected["api_format"] = api_format
    checkpoint = details.get("checkpoint")
    if isinstance(checkpoint, dict):
        from lib.domain.workflow_targets import STAGES
        if (isinstance(checkpoint.get("stage"), str) and checkpoint["stage"] in STAGES
                and isinstance(checkpoint.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", checkpoint["sha256"])):
            selected["call"] = {"stage": checkpoint["stage"], "sha256": checkpoint["sha256"]}
    return selected


def reasoning_provenance(selection: dict) -> dict:
    """Project trusted reasoning provenance into the auditable sample record."""
    result = {"reasoning_origin": selection["origin"]}
    if "summary" in selection:
        result["reasoning_summary"] = deepcopy(selection["summary"])
    if "api_format" in selection:
        result["reasoning_api_format"] = selection["api_format"]
    if "call" in selection:
        result["reasoning_call"] = deepcopy(selection["call"])
    return result
