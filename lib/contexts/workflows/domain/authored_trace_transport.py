"""Versioned transport normalization, separate from reasoning quality.

Only a list of explicit basis strings can become one multiline basis string.
Nothing is inferred, dropped, completed or marked semantically correct here.
Raw checkpoints remain immutable; the receipt records every representation change.
"""
from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from lib.domain.authored_reasoning import authored_reasoning_issue
from .dialogue import dialogue_digest


def authored_response_schema(*, answer_required: bool) -> dict:
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}

    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    def items(value, minimum=1):
        return {"type": "array", "items": value, "minItems": minimum}

    trace = obj({"task": text, "premises": items(text),
                 "steps": items(obj({"basis": text, "inference": text, "result": text})),
                 "checks": items(obj({"check": text, "result": text})),
                 "limitations": items(text, 0)})
    properties = {"reasoning": text, "reasoning_trace": trace}
    if answer_required:
        properties["answer"] = text
    schema = obj(properties)
    # Joint candidates historically permit unrelated candidate metadata.
    schema["additionalProperties"] = answer_required
    return schema


def authored_transport_instruction(*, answer_required: bool) -> str:
    return ("\n程序响应协议 v1：以下 JSON Schema 只约束传输字段，不规定思考顺序。"
            "reasoning 保留完整自然推导；不要在正文讨论协议。basis/inference/result 均为文本，"
            "多项依据写在同一个 basis 文本中，不需要编号。结构合格不代表评审通过。\n"
            + json.dumps(authored_response_schema(answer_required=answer_required), ensure_ascii=False))


def prepare_authored_response(candidate, *, answer_required: bool) -> tuple[object, dict]:
    """Return a new canonical envelope and a non-authorizing transport receipt."""
    canonical = deepcopy(candidate)
    receipt = {"version": 1, "status": "invalid", "semantic_status": "not_assessed",
               "raw_sha256": dialogue_digest(candidate), "changes": [], "errors": []}
    trace = canonical.get("reasoning_trace") if isinstance(canonical, dict) else None
    steps = trace.get("steps") if isinstance(trace, dict) else None
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            basis = step.get("basis") if isinstance(step, dict) else None
            if (isinstance(basis, list) and basis
                    and all(isinstance(item, str) and item.strip() and "\x00" not in item for item in basis)):
                # Newlines preserve each exact atom, its order and all whitespace.
                # No list item is discarded, paraphrased or promoted to a fact.
                step["basis"] = "\n".join(basis)
                receipt["changes"].append({"path": ["reasoning_trace", "steps", index, "basis"],
                                           "operation": "join_explicit_basis_lines", "original": deepcopy(basis)})
    validator = Draft202012Validator(authored_response_schema(answer_required=answer_required))
    receipt["errors"] = [{"path": list(error.absolute_path), "rule": error.validator,
                          "expected": deepcopy(error.validator_value)}
                         for error in validator.iter_errors(canonical)]
    receipt["canonical_sha256"] = dialogue_digest(canonical)
    if receipt["errors"]:
        return canonical, receipt
    # Keep existing content checks; joining a list cannot hide an empty,
    # circular or repeated assertion among otherwise concrete basis lines.
    content_issue = authored_reasoning_issue(canonical, answer_required=answer_required)
    if not content_issue:
        for change in receipt["changes"]:
            index = change["path"][2]
            atoms = change["original"]
            if len(set(atoms)) != len(atoms):
                content_issue = "authored_reasoning_repeated_basis"
                break
            for atom in atoms:
                trial = deepcopy(canonical)
                trial["reasoning_trace"]["steps"][index]["basis"] = atom
                content_issue = authored_reasoning_issue(trial, answer_required=answer_required)
                if content_issue:
                    break
            if content_issue:
                break
    receipt.update(status="normalized" if receipt["changes"] else "valid", content_issue=content_issue)
    return canonical, receipt
