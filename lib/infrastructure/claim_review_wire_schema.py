"""Pure JSON Schema guidance for explicitly opted-in review transports.

The provider still receives the ordinary json_object request. This schema is
readable wire guidance, not a claim that the upstream enforces JSON Schema.
The domain validator remains authoritative and never rewrites a raw reply.
"""
from __future__ import annotations

import re


# A program-only boundary in Workflow.ask's trusted instruction argument.
# Candidate/source text can never activate it or become system instructions.
CLAIM_REVIEW_DYNAMIC_WIRE_HEADER = "\n<claim_review_dynamic_wire_metadata>\n"


def source_leaf_paths(value):
    """List bounded, safe paths without copying source text into diagnostics."""
    paths, visited, truncated = [], 0, False

    def walk(part, path):
        nonlocal visited, truncated
        visited += 1
        if visited > 512 or len(paths) >= 64 or len(path) > 12:
            truncated = True
            return
        if isinstance(part, str):
            paths.append(path)
        elif isinstance(part, list):
            for index, child in enumerate(part[:64]):
                walk(child, [*path, index])
            truncated = truncated or len(part) > 64
        elif isinstance(part, dict):
            for key, child in part.items():
                if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,79}", key):
                    walk(child, [*path, key])
                else:
                    truncated = True

    walk(value, [])
    return {"actual_source_leaf_paths": paths, "source_leaf_paths_truncated": truncated}


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _text():
    return {"type": "string", "minLength": 1,
            "description": "Nonblank text without a NUL character; literal quotes must exist at their cited input path."}


def claim_review_response_schema(request):
    """Describe the complete protocol-four response, with current id enums."""
    if (type(request.get("claim_review_protocol_version")) is not int
            or request["claim_review_protocol_version"] != 4
            or request.get("phase") not in {"claim_challenge", "claim_adjudication"}):
        raise ValueError("invalid_claim_review_wire_schema_request")
    ids = [unit["id"] for unit in request["target_units"]]
    unit_id = {"type": "string", "enum": ids}
    source_reference = _object({"quote": _text(), "source_path": {
        "type": "array", "items": {"oneOf": [{"type": "string"}, {"type": "integer", "minimum": 0}]},
        "description": "JSON key/index array relative to the inside of source_evidence. Do not include source_evidence or untrusted_source_memory as a path component. Do not use a slash, dot, or bracket path string. Array indices are integers, not numeric strings."}})
    context_choices = []
    allowed_fields = {"role", "content", "name", "tool_calls", "toolCalls", "tool_call_id", "toolCallId",
                      "isError", "is_error", "reasoning_content", "reasoning", "thinking"}
    for index, message in enumerate(request["messages"][:request["scope"]["message_index"]]):
        fields = sorted(key for key in allowed_fields if isinstance(message.get(key), str))
        if fields:
            context_choices.append(_object({"quote": _text(), "message_index": {"type": "integer", "const": index},
                "field": {"type": "string", "enum": fields}}))
    context_reference = {"oneOf": context_choices} if context_choices else False
    basis_choices = []
    for kind in ("source", "context", "logic"):
        item = ({"$ref": "#/$defs/source_reference"} if kind == "source" else
                {"$ref": "#/$defs/context_reference"} if kind == "context" else
                {"oneOf": [{"$ref": "#/$defs/source_reference"}, {"$ref": "#/$defs/context_reference"}]})
        evidence = {"type": "array", "items": item}
        if kind != "logic":
            evidence["minItems"] = 1
        basis_choices.append(_object({"kind": {"type": "string", "const": kind},
            "evidence": evidence, "derivation": _text()}))
    score = {"type": "integer", "minimum": 1, "maximum": 5}
    verdict = _object({"keep": {"type": "boolean"}, "grounded": {"type": "boolean"},
        "reasoning_valid": {"type": "boolean"}, "correctness": score,
        "scores": _object({name: dict(score) for name in ("correctness", "reasoning", "grounding", "instruction", "safety")}),
        "reason": _text()})
    basis = {"$ref": "#/$defs/basis"}
    props = {"claim_review_protocol_version": {"type": "integer", "const": 4},
             "scope_sha256": {"type": "string", "const": request["scope"]["scope_sha256"]}}
    severity = {"type": "string", "enum": ["material", "minor"]}
    if request["phase"] == "claim_challenge":
        props.update(phase={"type": "string", "const": "claim_challenge"},
            issues={"type": "array", "items": _object({
                "id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "unit_id": unit_id, "severity": severity, "problem": _text(), "counterexample": _text(), "basis": basis})},
            limitations={"type": "array", "items": _text()})
    else:
        issues = request["challenge_review"]["issues"]
        decisions = [_object({"id": {"type": "string", "const": issue["id"]},
            "unit_id": {"type": "string", "const": issue["unit_id"]},
            "decision": {"type": "string", "enum": ["confirmed", "dismissed", "unresolved"]}, "basis": basis})
            for issue in issues]
        props.update(coverage={"type": "string", "enum": ["complete", "partial"]},
            checked_unit_ids={"type": "array", "items": unit_id, "uniqueItems": True,
                              "minItems": len(ids), "maxItems": len(ids)},
            claim_checks={"type": "array", "minItems": len(ids), "maxItems": len(ids),
                "items": _object({"unit_id": unit_id, "status": {"type": "string", "enum": ["supported", "refuted", "insufficient"]},
                                  "severity": severity, "basis": basis})},
            challenge_checks={"type": "array", "minItems": len(issues), "maxItems": len(issues),
                              "items": {"oneOf": decisions} if decisions else False},
            verdict=verdict)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **_object(props),
            "$defs": {"source_reference": source_reference, "context_reference": context_reference,
                      "basis": {"oneOf": basis_choices}}}


def claim_review_wire_schema_metadata(request):
    return {"claim_review_wire_schema_version": 1,
        "response_json_schema": claim_review_response_schema(request),
        "source_reference_root": "inside source_evidence; root names are not path components",
        **source_leaf_paths(request.get("source_evidence"))}
