"""Pure JSON Schema contracts for explicitly opted-in review transports.

The provider still receives the ordinary json_object request. This schema is
readable wire guidance, not a claim that the upstream enforces JSON Schema.
The domain validator remains authoritative and never rewrites a raw reply.
"""
from __future__ import annotations

import re

from jsonschema import Draft202012Validator


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
    all_ids = [unit["id"] for unit in request["target_units"]]
    window = request.get("review_window")
    ids = window["unit_ids"] if window is not None else all_ids
    condition_contract = request.get("condition_contract_version") == 1
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
    basis_context_choices = [choice for choice in context_choices if not condition_contract
        or request["messages"][choice["properties"]["message_index"]["const"]]["role"] != "assistant"]
    context_reference = {"oneOf": basis_context_choices} if basis_context_choices else False
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
    verdict = review_grade_schema()
    basis = {"$ref": "#/$defs/basis"}
    props = {"claim_review_protocol_version": {"type": "integer", "const": 4},
             "scope_sha256": {"type": "string", "const": request["scope"]["scope_sha256"]}}
    severity = {"type": "string", "enum": ["material", "minor"]}
    if window is not None:
        props["scope_sha256"] = {"type": "string", "const": window["sha256"]}
    if condition_contract:
        premise_choices = []
        use = {"type": "string", "enum": ["task_condition", "reference_resolution", "support", "counterexample"]}
        for origin in ("candidate", "context", "source", "assumption"):
            fields = {"origin": {"type": "string", "const": origin}, "use": use, "quote": _text()}
            if origin == "candidate":
                # Other spans remain available as literal condition anchors.
                fields["unit_id"] = {"type": "string", "enum": all_ids}
                fields["use"] = {"type": "string", "enum": ["task_condition", "reference_resolution", "counterexample"]}
            elif origin == "context":
                choices = []
                for context in context_choices:
                    properties = context["properties"]
                    index = properties["message_index"]["const"]
                    choices.append(_object({**fields,
                        "use": {"type": "string", "const": "reference_resolution"}
                            if request["messages"][index]["role"] == "assistant" else use,
                        "message_index": properties["message_index"], "field": properties["field"]}))
                premise_choices.extend(choices)
                continue
            elif origin == "source":
                fields["source_path"] = source_reference["properties"]["source_path"]
            premise_choices.append(_object(fields))
        premises = {"type": "array", "items": {"oneOf": premise_choices},
            "description": "All conditions actually needed by this judgment, with their literal provenance. New assumptions cannot prove the original claim; quotes bind location, not entailment."}
    if request["phase"] == "claim_challenge":
        props.update(phase={"type": "string", "const": "claim_challenge"},
            issues={"type": "array", "items": _object({
                "id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "unit_id": unit_id, "severity": severity, "problem": _text(), "counterexample": _text(), "basis": basis})},
            limitations={"type": "array", "items": _text()})
        if condition_contract:
            props["issues"] = {"type": "array", "maxItems": 0, "items": False}
            props["condition_ledger"] = {"type": "array", "minItems": len(ids), "maxItems": len(ids),
                "items": _object({"unit_id": unit_id, "premises": premises})}
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
        if condition_contract:
            fields = props["claim_checks"]["items"]["properties"]
            props["claim_checks"]["items"] = _object({**fields, "premises": premises})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **_object(props),
            "$defs": {"source_reference": source_reference, "context_reference": context_reference,
                      "basis": {"oneOf": basis_choices}}}


def claim_review_wire_schema_metadata(request):
    return {"claim_review_wire_schema_version": 1,
        "response_json_schema": claim_review_response_schema(request),
        "source_reference_root": "inside source_evidence; root names are not path components",
        **source_leaf_paths(request.get("source_evidence"))}


def validate_review_wire_response(response, request):
    """Check the full wire shape before provenance/semantic domain guards."""
    if not Draft202012Validator(claim_review_response_schema(request)).is_valid(response):
        raise ValueError("invalid_claim_review_wire_response")


def review_wire_diagnostics(response, request):
    """Schema-owned paths and expected fields only; never echo rejected text."""
    return {**schema_diagnostics(response, claim_review_response_schema(request)),
            **source_reference_diagnostics(response, request)}


def source_reference_diagnostics(response, request):
    """Locate exact quotations without relocating or changing model evidence.

    A wrong block index is not a fabricated quotation. The next actual model
    response must choose its reference again; the original response remains
    invalid. No fuzzy matching, quote normalization or semantic support is
    inferred here. Only schema-owned response paths and authentic source paths
    are returned, never rejected values or source text.
    """
    sources = []
    for path in source_leaf_paths(request.get("source_evidence"))["actual_source_leaf_paths"]:
        value = request["source_evidence"]
        for component in path:
            value = value[component]
        sources.append((path, value))
    failures, visited = [], 0

    def walk(value, path):
        nonlocal visited
        visited += 1
        if visited > 8192 or len(failures) >= 24 or len(path) > 16:
            return
        if isinstance(value, dict):
            if "source_path" in value and isinstance(value.get("quote"), str) and value["quote"].strip():
                matches = [source_path for source_path, source in sources if value["quote"] in source]
                if value["source_path"] not in matches:
                    failures.append({"response_path": [*path, "source_path"],
                        "code": "quote_at_other_source_path" if matches else "quote_not_in_source",
                        "exact_quote_source_paths": matches})
            for key, child in value.items():
                if key in {"claim_checks", "condition_ledger", "challenge_checks", "issues",
                           "premises", "basis", "evidence", "counter_evidence"}:
                    walk(child, [*path, key])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, [*path, index])

    walk(response, [])
    return {"unmatched_source_references": failures} if failures else {}


def review_grade_schema():
    score = {"type": "integer", "minimum": 1, "maximum": 5}
    return _object({"keep": {"type": "boolean"}, "grounded": {"type": "boolean"},
        "reasoning_valid": {"type": "boolean"}, "correctness": score,
        "scores": _object({name: dict(score) for name in ("correctness", "reasoning", "grounding", "instruction", "safety")}),
        "reason": _text()})


def schema_diagnostics(response, schema):
    failures = []
    for error in Draft202012Validator(schema).iter_errors(response):
        path = list(error.absolute_path)
        # Object keys supplied by a model may contain arbitrary source text.
        if any(isinstance(part, str) and not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", part)
               for part in path):
            path = []
        diagnostic = {"response_path": path, "rule": error.validator}
        if error.validator == "required" and isinstance(error.instance, dict):
            diagnostic["missing_fields"] = [name for name in error.validator_value if name not in error.instance]
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            diagnostic["allowed_fields"] = list(error.schema.get("properties", {}))
            diagnostic["unexpected_field_count"] = len(set(error.instance) - set(diagnostic["allowed_fields"]))
        elif error.validator == "type":
            diagnostic["expected_type"] = error.validator_value
        if diagnostic not in failures:
            failures.append(diagnostic)
        if len(failures) == 24:
            break
    return {"schema_diagnostics": failures}
