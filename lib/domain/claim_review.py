"""Exact, prefix-isolated claim-review receipts for new workflow recipes.

This module validates coverage, quotations and conservative decisions. It is
not a semantic theorem prover: a structurally valid citation can still be
misread by a reviewer. Raw model grades and the derived gate stay separate.
Historical recipes do not invoke this opt-in protocol.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from lib.domain.learner_context import learner_messages
from lib.domain.reasoning_fields import REASONING_FIELDS
from lib.domain.turn_review import supervised_assistant_turns, turn_review_verdict
from lib.domain.workflow_quality import accepted, verdict


CLAIM_REVIEW_PROTOCOL_VERSION = 1
_GRADE_FIELDS = frozenset({"keep", "grounded", "reasoning_valid", "correctness", "scores", "reason"})
_MESSAGE_FIELDS = frozenset({"role", "content", "name", "tool_calls", "toolCalls", "tool_call_id",
                             "toolCallId", "isError", "is_error", *REASONING_FIELDS})
_SOURCE_FIELDS = frozenset({"teacher_evidence", "teacher_evidence_blocks", "teacher_evidence_source_ids",
    "source_id", "source_kind", "source_line_ranges", "evidence_pack_sha256", "teacher_evidence_is_pack",
    "source_context_pack", "source_verification", "evidence_level", "text", "content", "blocks", "title",
    "name", "kind", "sources", "documents", "location", "source_line_scope", "supplemental_reference_sources"})
_RESPONSE_FIELDS = frozenset({"claim_review_protocol_version", "scope", "coverage", "checked_fields",
                              "claim_checks", "verdict"})
_PRIVATE_BOOKKEEPING = frozenset({"qa_contract", "qa_plan", "dialogue_dispatch", "dialogue_state", "dialogue_steps",
    "judge", "review", "reviews", "review_repair", "revision_context", "aggregate_original", "turn_verdicts",
    "claim_review", "reasoning_revision_audits", "generation_trace", "reasoning_trace", "prefix_review"})
_REQUIREMENT_FIELDS = frozenset({"generation_style", "learning_goal", "user_language", "preservation"})
_STYLE_FIELDS = frozenset({"stage", "preset", "style", "instruction", "reasoning_language", "answer_mode", "enabled", "assistant_indices"})
_PRESERVATION_FIELDS = frozenset({"reasoning_required", "preserve_reasoning", "preserve_user_messages", "preserve_source_attribution"})


def _digest(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("invalid_claim_review_json") from error
    return hashlib.sha256(raw).hexdigest()


def _text(value, error):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(error)
    return value


def _message_projection(message):
    if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
        raise ValueError("invalid_claim_review_message")
    return {key: deepcopy(value) for key, value in message.items() if key in _MESSAGE_FIELDS}


def _source_projection(evidence):
    def without_bookkeeping(value):
        if isinstance(value, dict):
            return {key: without_bookkeeping(part) for key, part in value.items() if key not in _PRIVATE_BOOKKEEPING}
        if isinstance(value, list):
            return [without_bookkeeping(part) for part in value]
        return deepcopy(value)

    if isinstance(evidence, dict):
        # The caller supplies source evidence, not the QA plan. Do not expose
        # score/repair metadata accidentally attached at its outer boundary.
        return {key: without_bookkeeping(value) for key, value in evidence.items() if key in _SOURCE_FIELDS}
    if evidence is None or isinstance(evidence, (str, list)):
        return without_bookkeeping(evidence)
    raise ValueError("invalid_claim_review_source_evidence")


def _target_fields(message, reasoning_required):
    fields = {}
    content = message.get("content")
    if content not in (None, ""):
        fields["content"] = _text(content, "invalid_claim_review_answer")
    for field in REASONING_FIELDS:
        value = message.get(field)
        if value not in (None, ""):
            fields[field] = _text(value, "invalid_claim_review_reasoning")
    for field in ("tool_calls", "toolCalls"):
        if message.get(field):
            if not isinstance(message[field], list):
                raise ValueError("invalid_claim_review_tool_calls")
            fields[field] = json.dumps(message[field], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if reasoning_required and not any(field in fields for field in REASONING_FIELDS):
        raise ValueError("claim_review_required_reasoning_missing")
    if not fields:
        raise ValueError("claim_review_empty_assistant")
    return fields


def _requirements(value):
    """Validate finite caller-owned node settings, without inventing defaults.

    The engine must obtain these from the frozen recipe, never a model's old
    candidate metadata. Validation does not authenticate their provenance.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - _REQUIREMENT_FIELDS:
        raise ValueError("invalid_claim_review_requirements")
    for key, part in value.items():
        if key == "generation_style":
            if not isinstance(part, dict) or set(part) - _STYLE_FIELDS:
                raise ValueError("invalid_claim_review_requirements")
            for field, setting in part.items():
                if field == "enabled":
                    if type(setting) is not bool:
                        raise ValueError("invalid_claim_review_requirements")
                elif field == "assistant_indices":
                    if (not isinstance(setting, list) or len(setting) > 10_000
                            or any(type(index) is not int or index < 0 for index in setting)
                            or len(setting) != len(set(setting))):
                        raise ValueError("invalid_claim_review_requirements")
                elif (not isinstance(setting, str) or "\x00" in setting
                      or len(setting) > (16_000 if field == "instruction" else 256)
                      or field != "instruction" and not setting.strip()):
                    raise ValueError("invalid_claim_review_requirements")
        elif key == "preservation":
            if (not isinstance(part, dict) or set(part) - _PRESERVATION_FIELDS
                    or any(type(flag) is not bool for flag in part.values())):
                raise ValueError("invalid_claim_review_requirements")
        elif (not isinstance(part, str) or not part.strip() or "\x00" in part
              or len(part) > (4096 if key == "learning_goal" else 128)):
            raise ValueError("invalid_claim_review_requirements")
    return deepcopy(value)


def claim_review_requests(target, payload, evidence, reasoning_required=False, review_requirements=None):
    """Build one review input per assistant, with no future turn or old grade.

    Hashes bind the exact original payload and raw original prefix, including
    metadata omitted from the model-facing projection. They authenticate no
    remote service and certify no factual truth. CoT lists are joined in full,
    while the original representation remains bound by candidate_sha256.
    """
    if not isinstance(target, str) or target not in {"sft", "multiturn", "cot"} or not isinstance(payload, dict) or not payload:
        raise ValueError("invalid_claim_review_candidate")
    if type(reasoning_required) is not bool:
        raise ValueError("invalid_claim_review_reasoning_required")
    requirements = _requirements(review_requirements)
    if target == "cot":
        reasoning = payload.get("reasoning", "")
        if isinstance(reasoning, list) and any(not isinstance(item, str) for item in reasoning):
            raise ValueError("invalid_claim_review_reasoning")
    messages = learner_messages(target, payload)
    if not isinstance(messages, list) or not messages:
        raise ValueError("claim_review_missing_messages")
    projected = [_message_projection(message) for message in messages]
    source = _source_projection(evidence)
    requests = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        fields = _target_fields(message, reasoning_required)
        visible = deepcopy(projected[:index + 1])
        scope = {"candidate_sha256": _digest(payload), "prefix_sha256": _digest(messages[:index + 1]),
                 "message_sha256": _digest(message), "source_input_sha256": _digest(evidence),
                 "source_evidence_sha256": _digest(source), "message_index": index,
                 "reasoning_required": reasoning_required}
        body = {"claim_review_protocol_version": CLAIM_REVIEW_PROTOCOL_VERSION, "target": target,
                "scope": scope, "messages": visible, "target_fields": fields,
                "source_evidence": deepcopy(source)}
        if requirements is not None:
            body["review_requirements"] = deepcopy(requirements)
        scope["scope_sha256"] = _digest(body)
        requests.append(body)
    if not requests:
        raise ValueError("claim_review_missing_assistant")
    return requests


def _validate_request(request):
    expected = {"claim_review_protocol_version", "target", "scope", "messages", "target_fields", "source_evidence"}
    if (not isinstance(request, dict) or set(request) not in (expected, expected | {"review_requirements"})
            or type(request["claim_review_protocol_version"]) is not int
            or request["claim_review_protocol_version"] != CLAIM_REVIEW_PROTOCOL_VERSION
            or not isinstance(request["target"], str) or request["target"] not in {"sft", "multiturn", "cot"}):
        raise ValueError("invalid_claim_review_request")
    if "review_requirements" in request and _requirements(request["review_requirements"]) is None:
        raise ValueError("invalid_claim_review_requirements")
    scope = request["scope"]
    scope_fields = {"candidate_sha256", "prefix_sha256", "message_sha256", "source_input_sha256",
                    "source_evidence_sha256", "message_index", "reasoning_required", "scope_sha256"}
    if not isinstance(scope, dict) or set(scope) != scope_fields:
        raise ValueError("invalid_claim_review_scope")
    if type(scope["message_index"]) is not int or scope["message_index"] < 0 or type(scope["reasoning_required"]) is not bool:
        raise ValueError("invalid_claim_review_scope")
    for field in scope_fields - {"message_index", "reasoning_required"}:
        value = scope[field]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("invalid_claim_review_scope")
    body = deepcopy(request)
    body["scope"].pop("scope_sha256")
    if _digest(body) != scope["scope_sha256"] or _digest(request["source_evidence"]) != scope["source_evidence_sha256"]:
        raise ValueError("stale_claim_review_request")
    messages = request["messages"]
    if not isinstance(messages, list) or len(messages) != scope["message_index"] + 1:
        raise ValueError("invalid_claim_review_prefix")
    for message in messages:
        if _message_projection(message) != message:
            raise ValueError("invalid_claim_review_message_fields")
    if messages[-1].get("role") != "assistant":
        raise ValueError("invalid_claim_review_target_role")
    if request["target_fields"] != _target_fields(messages[-1], scope["reasoning_required"]):
        raise ValueError("invalid_claim_review_target_fields")


def _reference(reference, request, kind):
    if not isinstance(reference, dict):
        raise ValueError("invalid_claim_review_evidence_reference")
    quote = _text(reference.get("quote"), "missing_claim_review_evidence_quote")
    if "source_path" in reference:
        if set(reference) != {"quote", "source_path"} or kind == "context":
            raise ValueError("invalid_claim_review_evidence_reference")
        path = reference["source_path"]
        if not isinstance(path, list):
            raise ValueError("invalid_claim_review_source_path")
        value = request["source_evidence"]
        try:
            for key in path:
                if isinstance(value, dict) and isinstance(key, str):
                    value = value[key]
                elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
                    value = value[key]
                else:
                    raise KeyError(key)
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("invalid_claim_review_source_path") from error
    else:
        if set(reference) != {"quote", "message_index", "field"} or kind == "source":
            raise ValueError("invalid_claim_review_evidence_reference")
        index, field = reference["message_index"], reference["field"]
        if (type(index) is not int or not 0 <= index < request["scope"]["message_index"]
                or not isinstance(field, str) or field not in _MESSAGE_FIELDS):
            raise ValueError("invalid_claim_review_context_reference")
        value = request["messages"][index].get(field)
    if not isinstance(value, str) or quote not in value:
        raise ValueError("fabricated_claim_review_evidence_quote")


def validate_claim_review(value, request, *, quote_matching_version=None):
    """Preserve the raw response, deriving a separate conservative gate.

    Missing detail is not reported as verified. Partial coverage and any
    material refutation/insufficiency veto release even when all raw scores
    are five. An explicitly rejected exploratory hypothesis is a semantic
    review decision; this contract does not reject words such as 'perhaps'.
    """
    if quote_matching_version is not None and (
            type(quote_matching_version) is not int or quote_matching_version != 1):
        raise ValueError("invalid_claim_review_quote_matching_version")
    _validate_request(request)
    if (not isinstance(value, dict) or set(value) != _RESPONSE_FIELDS
            or type(value["claim_review_protocol_version"]) is not int
            or value["claim_review_protocol_version"] != CLAIM_REVIEW_PROTOCOL_VERSION):
        raise ValueError("invalid_claim_review_response")
    if value["scope"] != request["scope"]:
        raise ValueError("stale_claim_review_response")
    if not isinstance(value["coverage"], str) or value["coverage"] not in {"complete", "partial"}:
        raise ValueError("invalid_claim_review_coverage")
    fields = value["checked_fields"]
    expected = set(request["target_fields"])
    if (not isinstance(fields, list) or any(not isinstance(field, str) for field in fields)
            or len(fields) != len(set(fields)) or not set(fields) <= expected):
        raise ValueError("invalid_claim_review_checked_fields")
    claims = value["claim_checks"]
    if not isinstance(claims, list) or not claims:
        raise ValueError("missing_claim_review_claims")
    seen, covered, material, quote_bindings = set(), set(), [], []
    for claim_index, claim in enumerate(claims):
        if not isinstance(claim, dict) or set(claim) != {"field", "quote", "status", "severity", "basis"}:
            raise ValueError("invalid_claim_review_claim")
        field, quote = claim["field"], _text(claim["quote"], "missing_claim_review_quote")
        if not isinstance(field, str) or field not in fields:
            raise ValueError("fabricated_claim_review_quote")
        if quote_matching_version == 1:
            from lib.domain.review_quote_binding import bind_candidate_quote
            quote_bindings.append(bind_candidate_quote(request["target_fields"][field], quote,
                field=field, response_path=["claim_checks", claim_index, "quote"], version=1))
        elif quote not in request["target_fields"][field]:
            raise ValueError("fabricated_claim_review_quote")
        signature = (field, quote)
        if signature in seen:
            raise ValueError("duplicate_claim_review_claim")
        seen.add(signature)
        covered.add(field)
        if (not isinstance(claim["status"], str) or claim["status"] not in {"supported", "refuted", "insufficient"}
                or not isinstance(claim["severity"], str) or claim["severity"] not in {"material", "minor"}):
            raise ValueError("invalid_claim_review_claim_status")
        basis = claim["basis"]
        if (not isinstance(basis, dict) or set(basis) != {"kind", "evidence", "derivation"}
                or not isinstance(basis["kind"], str) or basis["kind"] not in {"source", "context", "logic"}):
            raise ValueError("invalid_claim_review_basis")
        derivation = _text(basis["derivation"], "missing_claim_review_derivation")
        # These literal schema placeholders provide no inference at all. This
        # rejects only explicit vacuous placeholders, never words appearing
        # inside an otherwise specific discussion or rejected hypothesis.
        placeholder = derivation.strip().strip(".。!！ ").casefold()
        if placeholder in {"correct", "supported", "valid", "consistent", "looks good", "no issues",
                           "all good", "all correct", "正确", "有依据", "没有问题", "逻辑正确", "符合资料"} or derivation.strip() == quote.strip():
            raise ValueError("vacuous_claim_review_derivation")
        references = basis["evidence"]
        if not isinstance(references, list) or basis["kind"] != "logic" and not references:
            raise ValueError("missing_claim_review_evidence")
        for reference in references:
            _reference(reference, request, basis["kind"])
        if claim["severity"] == "material" and claim["status"] != "supported":
            material.append(claim)
    if covered != set(fields):
        raise ValueError("claim_review_unchecked_field")
    if value["coverage"] == "complete" and set(fields) != expected:
        raise ValueError("incomplete_claim_review_fields")
    raw_grade = value["verdict"]
    if not isinstance(raw_grade, dict) or set(raw_grade) != _GRADE_FIELDS:
        raise ValueError("invalid_claim_review_verdict_fields")
    derived = deepcopy(verdict(raw_grade))
    incomplete = value["coverage"] != "complete" or set(fields) != expected
    if material or incomplete:
        derived["keep"] = False
        if incomplete or any(item["basis"]["kind"] in {"source", "context"} for item in material):
            derived["grounded"] = False
        if incomplete or any(item["field"] in REASONING_FIELDS or item["basis"]["kind"] == "logic" for item in material):
            derived["reasoning_valid"] = False
        details = (["Claim coverage is partial; unexamined fields remain unverified."] if incomplete else [])
        details.extend(f"{item['field']}: {item['status']} material claim {item['quote']!r}: {item['basis']['derivation']}"
                       for item in material)
        derived["reason"] = "; ".join(details)
    turn = {**deepcopy(derived), "message_index": request["scope"]["message_index"],
            "content_sha256": request["scope"]["prefix_sha256"],
            "leakage_free": derived["reasoning_valid"], "critical_error": bool(material) or incomplete}
    return {"claim_review_protocol_version": CLAIM_REVIEW_PROTOCOL_VERSION,
            "request": deepcopy(request), "raw_response": deepcopy(value), "verdict": derived,
            "turn_verdict": turn, "accepted": accepted(derived) and not incomplete and not material,
            "structure_verified": True, "semantic_verification": "reviewer_reported_not_independently_verified",
            **({"quote_matching_version": 1, "quote_bindings": quote_bindings}
               if quote_matching_version == 1 else {})}


def merge_claim_reviews(payload, receipts):
    """Revalidate every receipt and merge only exact, fully covered turns.

    Raw responses are retained in separate caller-owned audit records. The
    return value is explicitly a derived verdict using the existing consumer
    shape; no score is rewritten or represented as a fresh upstream score.
    """
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("missing_claim_review_receipts")
    checked, expected_requests = [], None
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("invalid_claim_review_receipt")
        request = receipt.get("request")
        if not isinstance(request, dict):
            raise ValueError("invalid_claim_review_receipt")
        expected = validate_claim_review(receipt.get("raw_response"), request,
            quote_matching_version=receipt.get("quote_matching_version"))
        if expected != receipt:
            raise ValueError("altered_claim_review_receipt")
        if request["scope"]["candidate_sha256"] != _digest(payload):
            raise ValueError("stale_claim_review_candidate")
        # Original evidence may contain excluded outer bookkeeping. Compare
        # the exposed source and all prefix/body bindings independently.
        rebuilt = claim_review_requests(request["target"], payload, request["source_evidence"],
                                        request["scope"]["reasoning_required"], request.get("review_requirements"))
        expected_scope = deepcopy(request["scope"])
        expected_scope.pop("source_input_sha256")
        expected_scope.pop("scope_sha256")
        match = next((item for item in rebuilt if item["scope"]["message_index"] == request["scope"]["message_index"]), None)
        if match is None:
            raise ValueError("stale_claim_review_prefix")
        actual_scope = deepcopy(match["scope"])
        actual_scope.pop("source_input_sha256")
        actual_scope.pop("scope_sha256")
        if (actual_scope != expected_scope or match["messages"] != request["messages"]
                or match["target_fields"] != request["target_fields"]):
            raise ValueError("stale_claim_review_prefix")
        signature = (request["target"], request["scope"]["source_input_sha256"],
                     request["scope"]["source_evidence_sha256"], request["scope"]["reasoning_required"],
                     _digest(request.get("review_requirements")))
        if expected_requests is None:
            expected_requests = (signature, rebuilt)
        if signature != expected_requests[0]:
            raise ValueError("inconsistent_claim_review_scopes")
        checked.append(expected)
    indices = [item["turn_verdict"]["message_index"] for item in checked]
    expected_indices = [item["scope"]["message_index"] for item in expected_requests[1]]
    if len(set(indices)) != len(indices) or sorted(indices) != expected_indices:
        raise ValueError("incomplete_claim_review_turns")
    checked.sort(key=lambda item: item["turn_verdict"]["message_index"])
    first = checked[0]["verdict"]
    aggregate = {"keep": all(item["verdict"]["keep"] for item in checked),
                 "grounded": all(item["verdict"]["grounded"] for item in checked),
                 "reasoning_valid": all(item["verdict"]["reasoning_valid"] for item in checked),
                 "correctness": min(item["verdict"]["correctness"] for item in checked),
                 "scores": {name: min(item["verdict"]["scores"][name] for item in checked) for name in first["scores"]},
                 "reason": "; ".join(f"assistant[{item['turn_verdict']['message_index']}]: {item['verdict']['reason']}"
                                     for item in checked)}
    if checked[0]["request"]["target"] == "cot":
        return aggregate
    raw = {**aggregate, "turn_verdicts": [item["turn_verdict"] for item in checked]}
    merged = turn_review_verdict(raw, supervised_assistant_turns(payload))
    return {key: deepcopy(merged[key]) for key in (*_GRADE_FIELDS, "turn_verdicts")}
