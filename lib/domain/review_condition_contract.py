"""Exact premise provenance for opt-in condition-aware review receipts.

These helpers bind a reviewer's declared premises to visible text. They do not
prove entailment, discover omitted premises, or decide whether a claim is true.
An assumption remains a new assumption even when it makes a conditional version
of a candidate plausible. Original model input and output are never rewritten.

The caller owns the recipe opt-in and the outer protocol/hash validation. Older
protocol-four receipts need not and must not pass through this new contract.
"""
from copy import deepcopy

from lib.domain import claim_review
from lib.domain.review_target_units import validate_target_units


SEMANTIC_VERIFICATION = "provenance_only_not_entailment"
_USES = frozenset({"task_condition", "reference_resolution", "support", "counterexample"})
_LOCATORS = {
    "candidate": {"unit_id"},
    "context": {"message_index", "field"},
    "source": {"source_path"},
    "assumption": set(),
}


def _units(request):
    """Validate the exact current spans and the prefix needed by references."""
    if not isinstance(request, dict) or not isinstance(request.get("scope"), dict):
        raise ValueError("invalid_condition_contract_request")
    index = request["scope"].get("message_index")
    messages = request.get("messages")
    if (type(index) is not int or index < 0 or not isinstance(messages, list)
            or len(messages) != index + 1
            or any(not isinstance(message, dict) or message.get("role") not in
                   {"system", "user", "assistant", "tool"} for message in messages)
            or messages[-1]["role"] != "assistant"):
        raise ValueError("invalid_condition_contract_prefix")
    contract_version = request.get("condition_contract_version")
    if contract_version is not None and (type(contract_version) is not int or contract_version != 1):
        raise ValueError("invalid_condition_contract_version")
    try:
        units = validate_target_units(request.get("target_units"), request.get("target_fields"), index,
            segmentation_version=2 if contract_version == 1 else None)
    except ValueError as error:
        raise ValueError("invalid_condition_contract_units") from error
    return {unit["id"]: unit for unit in units}


def validate_premises(premises, request):
    """Return separate provenance annotations for an unchanged premise array.

    ``required_assumptions`` contains newly invented assumptions used for
    support, task conditions, or reference resolution. ``counterexample_assumptions``
    contains explicitly hypothetical counterexample premises. The latter are
    legitimate inputs for testing a claim, but cannot support a ``supported``
    verdict: a caller assessing that verdict must consider both lists.

    A candidate may state a condition or supply a referent; its assertion is not
    independent factual support. Earlier assistant text can resolve references
    only. User/tool/source quotations still establish provenance, not truth.
    """
    return _validate_premises(premises, request, _units(request))


def _validate_premises(premises, request, units):
    if not isinstance(premises, list):
        raise ValueError("invalid_condition_premises")
    required, counterexamples = [], []
    for index, premise in enumerate(premises):
        if not isinstance(premise, dict):
            raise ValueError("invalid_condition_premise")
        origin, use = premise.get("origin"), premise.get("use")
        if (not isinstance(origin, str) or origin not in _LOCATORS
                or not isinstance(use, str) or use not in _USES
                or set(premise) != {"origin", "use", "quote"} | _LOCATORS[origin]):
            raise ValueError("invalid_condition_premise")
        quote = claim_review._text(premise["quote"], "missing_condition_premise_quote")
        if origin == "candidate":
            identifier = premise["unit_id"]
            if not isinstance(identifier, str) or identifier not in units:
                raise ValueError("invalid_condition_candidate_unit")
            if quote not in units[identifier]["text"]:
                raise ValueError("fabricated_condition_candidate_quote")
            if use == "support":
                raise ValueError("condition_candidate_cannot_self_support")
        elif origin == "context":
            reference = {key: deepcopy(premise[key]) for key in ("quote", "message_index", "field")}
            claim_review._reference(reference, request, "context")
            message = request["messages"][premise["message_index"]]
            if message["role"] == "assistant" and use != "reference_resolution":
                raise ValueError("condition_historical_assistant_not_evidence")
        elif origin == "source":
            claim_review._reference({"quote": quote, "source_path": deepcopy(premise["source_path"])},
                                    request, "source")
        else:
            annotation = {"premise_index": index, "premise": deepcopy(premise)}
            (counterexamples if use == "counterexample" else required).append(annotation)
    return {"premises": deepcopy(premises), "required_assumptions": required,
            "counterexample_assumptions": counterexamples,
            "semantic_verification": SEMANTIC_VERIFICATION}


def condition_ledger(value, request):
    """Bind one premise declaration to every current unit, preserving raw order.

    ``value`` is an array containing exactly ``unit_id`` and ``premises`` for
    each current unit, once each. Empty premise lists are permitted: ordinary
    advice need not invent supporting conditions. Empty declarations are not a
    semantic certificate that no hidden condition was used.
    """
    units = _units(request)
    if not isinstance(value, list):
        raise ValueError("invalid_condition_ledger")
    seen, normalized, required, counterexamples = set(), [], [], []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"unit_id", "premises"}:
            raise ValueError("invalid_condition_ledger_entry")
        identifier = entry["unit_id"]
        if not isinstance(identifier, str) or identifier not in units or identifier in seen:
            raise ValueError("invalid_condition_ledger_coverage")
        seen.add(identifier)
        checked = _validate_premises(entry["premises"], request, units)
        normalized.append({"unit_id": identifier, **checked})
        required.extend({"unit_id": identifier, **deepcopy(item)}
                        for item in checked["required_assumptions"])
        counterexamples.extend({"unit_id": identifier, **deepcopy(item)}
                               for item in checked["counterexample_assumptions"])
    if seen != set(units):
        raise ValueError("invalid_condition_ledger_coverage")
    return {"units": normalized, "required_assumptions": required,
            "counterexample_assumptions": counterexamples,
            "semantic_verification": SEMANTIC_VERIFICATION}
