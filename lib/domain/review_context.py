"""Keep revision receipts private without changing saved lineage or evidence."""
from copy import deepcopy


REVIEW_CONTEXT_PROTOCOL_VERSION = 2
_HISTORICAL_FIELDS = frozenset({
    "revision_context", "review_repair", "judge", "qa_contract_check", "learner_context_check",
    "reasoning_prefill", "reasoning_prefill_history", "answer_response_details", "prefix_review",
    "provider_reasoning_history", "native_regeneration", "native_regeneration_history",
    "generation_trace", "generation_trace_content_sha256", "reasoning_trace", "reasoning_trace_content_sha256",
    "reasoning_summary", "reasoning_api_format", "reasoning_call", "turn_reviews",
    "native_answer_terminal", "native_answer_retry",
    "reasoning_revision_audits", "assistant_turn_review_retry", "aggregate_original",
})
_REASONING_FIELDS = frozenset({"reasoning_content", "reasoning", "thinking"})


def review_contract(contract):
    """Copy task/design fields while excluding nested old response receipts."""
    if isinstance(contract, dict):
        return {key: review_contract(value) for key, value in contract.items() if key not in _HISTORICAL_FIELDS}
    if isinstance(contract, list):
        return [review_contract(value) for value in contract]
    return deepcopy(contract)


def _multiturn_repair_candidate(candidate):
    projected = deepcopy(candidate)
    if not isinstance(projected, dict) or not isinstance(projected.get("messages"), list):
        return projected
    for message in projected["messages"]:
        if isinstance(message, dict) and message.get("role") == "assistant":
            for field in _REASONING_FIELDS:
                message.pop(field, None)
    return projected


def project_review_request(recipe, data, *, phase):
    """New requests exclude historical lineage; legacy payloads stay literal.

    Current answer/reasoning remain available to the fact grader. Version 1
    multiturn repairs omit obsolete reasoning; version 2 repairs read it as
    untrusted editable material and must return a complete replacement.
    visible replies, role/tool records and message positions stay unchanged.
    Independent source_evidence is neither filtered nor promoted by this view.
    """
    version = recipe.get("review_context_protocol_version")
    if version not in {1, REVIEW_CONTEXT_PROTOCOL_VERSION}:
        return data
    projected = dict(data)
    if "qa_contract" in data:
        projected["qa_contract"] = review_contract(data["qa_contract"])
    if version == 1 and phase == "repair" and data.get("target") == "multiturn" and "candidate" in data:
        projected["candidate"] = _multiturn_repair_candidate(data["candidate"])
    return projected
