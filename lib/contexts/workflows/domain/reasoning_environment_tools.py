"""Stable requirement IDs and one citation shape for the version 5 review protocol."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from .reasoning_tool_contracts import evidence_tools, obj, array, enum, NONEMPTY, CITATION
from .reasoning_claims import DECIMAL


def environment_tools():
    tools = deepcopy(evidence_tools())
    operand = obj(source=NONEMPTY, quote=NONEMPTY, value=NONEMPTY)
    tools.extend([
        {"type": "function", "function": {"name": "inspect_claim",
         "description": "Read the exact claim and its complete paragraph, with all competing quantities. Use for cross-sentence references or quotation binding; this does not decide the reference or verdict.",
         "parameters": obj(anchor_id=NONEMPTY)}},
        {"type": "function", "function": {"name": "compare_claim_ratio",
         "description": "Compute a cited part/whole ratio when the claim has no literal numeric reported value, e.g. about half. If the claim has one strict half comparison, also compare it. Quote exact operands. Result is conditional arithmetic, NOT proof of the antecedent or approximate wording. Do not invent a numeric reported value for a verbal claim.",
         "parameters": obj(anchor_id=NONEMPTY, numerator=operand, denominator=operand)}},
    ])
    check = next(t["function"]["parameters"] for t in tools if t["function"]["name"] == "record_check")
    check["properties"]["evidence"] = array(CITATION)
    check["properties"]["observations"] = array(obj(
        requirement_id=NONEMPTY, status=enum("satisfied", "violated", "unknown", "not_applicable"),
        evidence=array(CITATION), reason=NONEMPTY), minItems=1)
    for tool in tools:
        spec = tool["function"]
        if spec["name"] == "submit_review":
            spec["parameters"]["properties"]["checks"]["items"] = deepcopy(check)
        if spec["name"] in {"record_check", "submit_review"}:
            spec["description"] += " Use active requirement_id values from environment; do not retype requirements. All citations use source,quote."
    # Exact registered spans are an alternative spelling of the same literal
    # citation, not new evidence or a semantic waiver.
    citation_ref = obj(citation_id=NONEMPTY)
    def describe(schema):
        if not isinstance(schema, dict):
            return
        for key, value in list(schema.get("properties", {}).items()):
            # Schema constants are shared by multiple properties. Never attach
            # a numeric restriction to a shared source/quote string object.
            value = deepcopy(value)
            schema["properties"][key] = value
            if key == "evidence":
                value["items"] = {"oneOf": [deepcopy(CITATION), deepcopy(citation_ref)]}
                value["description"] = "Use an existing claim anchor ID as citation_id for its exact body quote, or source/quote for an exact custom/source span. IDs never establish entailment."
            elif key == "counterexample":
                value["description"] = "If the selected anchor has scope=true, provide a nonempty actual scope/condition/counterexample check. Do not omit it or invent new premises. Otherwise explain why no scope test applies, or leave empty."
            elif key == "no_calculation_reason":
                value["description"] = "Required and nonempty when anchor.numeric=true and calculations is empty. Explain why the actual quantity needs no arithmetic; do not omit numerical checks."
            elif key in {"baseline", "value", "reported"}:
                value["pattern"] = "^" + DECIMAL.pattern + "$"
                value["description"] = "Exact decimal string only: no %, commas, words or inferred numbers. Keep original punctuation/units in quote fields. reported must literally occur in the claim; use compare_claim_ratio when the claim is verbal, such as about half."
            elif key == "interpretation" and "context_quote" in value.get("properties", {}):
                alternative = deepcopy(value)
                alternative["properties"].pop("context_quote")
                alternative["properties"]["context_citation_id"] = NONEMPTY
                alternative["required"] = ["context_citation_id" if x == "context_quote" else x for x in alternative["required"]]
                schema["properties"][key] = {"oneOf": [value, alternative]}
            else:
                describe(value)
        describe(schema.get("items"))
    for tool in tools:
        describe(tool["function"]["parameters"])
    return tools


def environment_tool_input(name, arguments):
    schema = next((t["function"]["parameters"] for t in environment_tools() if t["function"]["name"] == name), None)
    if schema is None:
        return [{"path": "$", "message": "Unknown review tool"}]
    return [{"path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path),
             "message": error.message} for error in Draft202012Validator(schema).iter_errors(arguments)]


def normalize_citation_keys(arguments):
    """Lossless legacy alias only; never change text, evidence source or verdict."""
    value, repairs = deepcopy(arguments), []
    def visit(item, path):
        if isinstance(item, dict):
            for key, child in list(item.items()):
                if key == "evidence" and isinstance(child, list):
                    for i, cite in enumerate(child):
                        if isinstance(cite, dict) and set(cite) == {"field", "quote"}:
                            cite["source"] = cite.pop("field")
                            repairs.append({"path": f"{path}.evidence[{i}]", "operation": "field_to_source", "values_unchanged": True})
                else:
                    visit(child, path + "." + key)
        elif isinstance(item, list):
            for i, child in enumerate(item):
                visit(child, path + f"[{i}]")
    visit(value, "$")
    return value, repairs
