"""Version 4 tool schemas and lossless envelope repair, with jsonschema diagnostics."""
from copy import deepcopy

from jsonschema import Draft202012Validator


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
NONEMPTY = {"type": "string", "minLength": 1}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def array(items, **options):
    return {"type": "array", "items": items, **options}


CITATION = obj(source=NONEMPTY, quote=NONEMPTY)
BODY_CITATION = obj(field=enum("reasoning", "answer"), quote=NONEMPTY)
CALCULATION = obj(
    operation=enum("difference", "ratio", "relative_change", "percent_of"),
    baseline=NONEMPTY, value=NONEMPTY, reported=NONEMPTY, reported_quote=NONEMPTY,
    metric_kind=enum("score", "proportion", "quantity"),
    operand_scale=enum("native", "percent", "fraction"),
    reported_unit=enum("score_point", "percentage_point", "percent", "ratio", "native"))
CHECK = obj(rule_id=NONEMPTY, status=enum("met", "unmet", "not_applicable"),
            evidence=array(BODY_CITATION), reason=NONEMPTY)
CLAIM = obj(anchor_id=NONEMPTY, status=enum("supported", "unsupported", "uncertain", "not_claim"),
            evidence=array(CITATION), reason=NONEMPTY, counterexample=TEXT,
            calculations=array(CALCULATION), no_calculation_reason=TEXT)
OBSERVATION = obj(requirement_quote=NONEMPTY, status=enum("satisfied", "violated", "unknown", "not_applicable"),
                  evidence=array(BODY_CITATION), reason=NONEMPTY)
CHECK_SUBMISSION = obj(**CHECK["properties"], observations=array(OBSERVATION, minItems=1))
INTERPRETATION = obj(stance=enum("asserted", "hypothetical", "negated", "quoted", "non_propositional"),
                     context_quote=NONEMPTY, explanation=NONEMPTY)
CLAIM_SUBMISSION = obj(**CLAIM["properties"], interpretation=INTERPRETATION)


def evidence_tools():
    specs = [
        ("submit_review", "Submit any ready checks/claims together. Each entry is independently validated; valid entries survive errors in siblings. No fixed order; do not repeat committed entries.",
         obj(checks=array(CHECK_SUBMISSION, maxItems=32), claims=array(CLAIM_SUBMISSION, maxItems=64))),
        ("record_check", "Submit a rule with separate observations of its requirements. A violated or unknown requirement cannot be waived by a positive vote.", CHECK_SUBMISSION),
        ("record_claim", "Bind a claim to its full context and stance. A quoted or denied number is not the author's asserted calculation.", CLAIM_SUBMISSION),
        ("read_source", "Read an exact range only if its full text is not already supplied. Adjacent context matters.",
         obj(source_id=NONEMPTY, start={"type": "integer", "minimum": 0}, length={"type": "integer", "minimum": 1, "maximum": 10000})),
        ("search_sources", "Literal search for a specific phrase. Do not submit a bag of keywords. No hit proves nothing. Read the supplied text before searching again.", obj(query={"type": "string", "minLength": 1, "maxLength": 200})),
        ("calculate_claim", "Optional arithmetic preview; record_claim calculates again locally at no model cost. Does not submit a verdict. Only calculate the author's asserted quantity, never a number explicitly rejected in context.",
         obj(anchor_id=NONEMPTY, calculation=CALCULATION, evidence=array(CITATION))),
        ("finish_review", "Finish only with complete evidence or a recorded defect/unresolved finding. Missing evidence never passes.", obj()),
    ]
    return [{"type": "function", "function": {"name": name, "description": description, "parameters": schema}}
            for name, description, schema in specs]


def tool_input(name, arguments):
    """Repair only a missing single wrapper; never edit evidence, votes, values or units."""
    schema = next((t["function"]["parameters"] for t in evidence_tools() if t["function"]["name"] == name), None)
    if schema is None:
        return arguments, [], [{"path": "$", "message": "Unknown review tool"}]
    value = deepcopy(arguments)
    repairs = []
    field = {"record_check": "check", "record_claim": "claim"}.get(name)
    if field and isinstance(value, dict):
        entry_keys = set((CHECK if field == "check" else CLAIM)["properties"])
        envelope_keys = set(schema["properties"]) - entry_keys
        if set(value) == {field} | envelope_keys and isinstance(value[field], dict):
            # Accept the old envelope only when flattening cannot overwrite a key.
            if not set(value[field]).intersection(envelope_keys):
                value = {**value[field], **{k: value[k] for k in envelope_keys}}
                repairs.append({"operation": "flatten_envelope", "field": field, "values_unchanged": True})
    errors = [{"path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path),
               "message": e.message} for e in Draft202012Validator(schema).iter_errors(value)]
    if field and not errors:
        value = {field: {k: value[k] for k in entry_keys}, **{k: value[k] for k in envelope_keys}}
        repairs.append({"operation": "pack_validated_record", "field": field, "values_unchanged": True})
    return value, repairs, errors
