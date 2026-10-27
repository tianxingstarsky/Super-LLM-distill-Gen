"""Exact context navigation and conditional arithmetic, never automatic coreference proof."""
from copy import deepcopy
from decimal import Decimal, localcontext
import re

from .reasoning_assertions import HALF
from .reasoning_claims import DECIMAL, _number_present


def inspect_claim(anchors, candidate, anchor_id):
    anchor = next((a for a in anchors if a["id"] == anchor_id), None)
    if anchor is None:
        return {"ok": False, "issue": "unknown_evidence_id"}
    text, at = candidate[anchor["field"]], anchor["start"]
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at + len(anchor["quote"]))
    end = len(text) if end < 0 else end
    paragraph = text[start:end]
    return {"ok": True, "anchor": deepcopy(anchor), "source": anchor["field"],
            "start": start, "end": end, "text": paragraph,
            "quantities": [{"quote": m[0], "start": start + m.start(), "end": start + m.end()}
                           for m in re.finditer(r"\d[\d,]*(?:\.\d+)?\s*[%％]?", paragraph)],
            "binding_questions": ["Which noun phrase does the reference denote? Quote its antecedent.",
                "Which population is the denominator? Distinguish each competing quantity in this paragraph.",
                "Is the antecedent explicitly present, or would choosing it add an unstated condition?"],
            "limits": "Navigation only. Adjacency, an exact quotation or a valid ratio does not prove semantic identity. Ambiguous reference remains uncertain."}


def compare_claim_ratio(anchors, candidate, passages, arguments):
    anchor = next((a for a in anchors if a["id"] == arguments["anchor_id"]), None)
    if anchor is None:
        return {"ok": False, "issue": "unknown_evidence_id"}
    comparisons = list(HALF.finditer(anchor["quote"]))
    sources = {p["id"]: p["text"] for p in passages} | candidate
    for key in ("numerator", "denominator"):
        operand = arguments[key]
        if operand["quote"] not in sources.get(operand["source"], ""):
            return {"ok": False, "issue": "claim_unbound_evidence", "path": key}
        if not DECIMAL.fullmatch(operand["value"]) or not _number_present(operand["value"], operand["quote"]):
            return {"ok": False, "issue": "calculation_uncited_operand", "path": key}
    numerator, denominator = (Decimal(arguments[k]["value"]) for k in ("numerator", "denominator"))
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        return {"ok": False, "issue": "invalid_part_whole_counts"}
    phrase = comparisons[0][0].lower() if len(comparisons) == 1 else None
    with localcontext() as context:
        context.prec = 60
        percent = numerator / denominator * 100
    holds = (None if phrase is None else percent < 50 if phrase in {"不到一半", "少于一半", "less than half"} else
             percent > 50 if phrase in {"超过一半", "多于一半", "more than half"} else
             percent >= 50 if phrase in {"至少一半", "at least half"} else percent <= 50)
    return {"ok": True, "anchor_id": anchor["id"], "operands": deepcopy(arguments),
            "percent": str(percent), "comparison_quote": comparisons[0][0] if phrase else None, "comparison_holds": holds,
            "semantic_binding": "unverified", "candidate_verdict": None,
            "limits": "Conditional arithmetic only: without a unique strict comparison, no verbal verdict is produced. Approximate wording needs semantic review. Bind subject, population and antecedent from exact context; never add a premise to make a comparison hold."}


def citation_diagnostics(evidence, sources, *, path="$.evidence"):
    """Locate exact wrong-field matches without copying, correcting or endorsing them."""
    details = []
    for i, cite in enumerate(evidence):
        source, quote = cite.get("source", cite.get("field")), cite.get("quote", "")
        if source in sources and quote and quote in sources[source]:
            continue
        matches = [{"source": key, "start": text.index(quote), "end": text.index(quote) + len(quote)}
                   for key, text in sources.items() if quote and quote in text]
        details.append({"path": f"{path}[{i}]", "submitted": deepcopy(cite),
            "exact_matches_elsewhere": matches, "available_sources": list(sources),
            "diagnosis": "wrong_source_field" if matches else "quotation_not_present",
            "message": "No citation was changed. Inspect the actual text and intended claim. Exact matches locate text; they do not establish truth or entailment."})
    return details


def resolve_citation_ids(arguments, anchors):
    """Expand only registered body-span IDs; unknown IDs never become free text."""
    registry = {a["id"]: {"source": a["field"], "quote": a["quote"]} for a in anchors}
    value, repairs, errors = deepcopy(arguments), [], []
    def visit(item, path):
        if isinstance(item, dict):
            for key, child in list(item.items()):
                if key == "evidence" and isinstance(child, list):
                    for i, cite in enumerate(child):
                        if isinstance(cite, dict) and set(cite) == {"citation_id"}:
                            identity = cite["citation_id"]
                            if not isinstance(identity, str) or identity not in registry:
                                errors.append({"path": f"{path}.evidence[{i}]", "message": "Unknown citation ID; use a supplied anchor ID or exact source/quote."})
                            else:
                                child[i] = deepcopy(registry[identity])
                                repairs.append({"path": f"{path}.evidence[{i}]", "citation_id": identity, "resolved": deepcopy(child[i]), "operation": "resolve_exact_span"})
                elif key == "interpretation" and isinstance(child, dict) and "context_citation_id" in child:
                    identity = child["context_citation_id"]
                    if "context_quote" in child or not isinstance(identity, str) or identity not in registry:
                        errors.append({"path": path + ".interpretation", "message": "Unknown/conflicting context citation ID; choose one exact context representation."})
                    else:
                        child.pop("context_citation_id")
                        child["context_quote"] = registry[identity]["quote"]
                        repairs.append({"path": path + ".interpretation", "citation_id": identity, "operation": "resolve_exact_context"})
                else:
                    visit(child, path + "." + key)
        elif isinstance(item, list):
            for i, child in enumerate(item):
                visit(child, path + f"[{i}]")
    visit(value, "$")
    return value, repairs, errors
