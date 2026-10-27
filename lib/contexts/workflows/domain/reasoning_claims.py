"""Locate risky clauses and verify cited, typed arithmetic without executing code."""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import re

DECIMAL = re.compile(r"[+-]?\d{1,30}(?:\.\d{1,20})?")
STRENGTH = re.compile(
    r"必然|一定|只有|所有|全部|永远|任何|不能|不存在|未做|未提|唯一|保证|导致|证明|高估|低估|"
    r"\b(?:all|always|never|only|must|cannot|can't|proves?|guarantees?|necessarily|causes?|no|every)\b", re.I)


def _number_present(value, text):
    # Canonicalize only unambiguous thousands groups and the mathematical minus.
    # Preserve signs: +6.8 and -6.8 are not interchangeable evidence.
    text = re.sub(r"(?<![\d,.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,.])",
                  lambda m: m[0].replace(",", ""), text).replace("−", "-")
    tokens = re.findall(r"(?<![\d.])[+-]?\d+(?:\.\d+)?(?![\d.])", text)
    return any(Decimal(t) == Decimal(value) for t in tokens)


def claim_anchors(candidate):
    """An inventory, not a semantic detector. Reviewers must also scan the full text."""
    anchors = []
    for field in ("reasoning", "answer"):
        text = candidate.get(field, "")
        if not isinstance(text, str):
            raise ValueError("claim_body_not_text")
        start = 0
        for boundary in [*re.finditer(r"[。！？!?](?:\s*)|[.;](?:\s+)|\n+", text), None]:
            end = boundary.end() if boundary else len(text)
            raw = text[start:end]
            quote = raw.strip()
            if quote and (re.search(r"\d", quote) or STRENGTH.search(quote)):
                offset = start + len(raw) - len(raw.lstrip())
                anchors.append({"id": "c_" + hashlib.sha256(f"{field}:{offset}:{quote}".encode()).hexdigest()[:16],
                    "field": field, "start": offset, "quote": quote,
                    "numeric": bool(re.search(r"\d", quote)), "scope": bool(STRENGTH.search(quote))})
            start = end
    return anchors


def source_passages(context):
    """Exclude reviewer feedback/style instructions from possible factual sources."""
    passages = []
    def visit(value, path):
        if isinstance(value, str) and value.strip():
            passages.append({"id": path, "text": value})
        elif isinstance(value, dict):
            for key, item in value.items():
                if key not in {"feedback", "style", "instruction", "reasoning_trace", "reasoning_content"}:
                    visit(item, path + "." + key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, path + f"[{i}]")
    for key in ("source", "source_context", "prompt", "question", "messages"):
        if key in context:
            visit(context[key], key)
    return passages


def verify_calculation(calc, evidence_text, claim_text=None):
    """No eval, metric-name guessing or model-supplied tolerance. Return a receipt."""
    fields = {"operation", "baseline", "value", "reported", "reported_quote", "metric_kind", "operand_scale", "reported_unit"}
    if not isinstance(calc, dict) or set(calc) != fields:
        return {"passed": False, "issue": "calculation_schema"}
    for key in ("baseline", "value", "reported"):
        if not isinstance(calc[key], str) or not DECIMAL.fullmatch(calc[key]):
            return {"passed": False, "issue": "calculation_decimal"}
        # Require the literal number in a cited passage, not a silently invented operand.
        if not _number_present(calc[key], evidence_text):
            return {"passed": False, "issue": "calculation_uncited_operand", "operand": key}
    kind, scale, unit, op = (calc[k] for k in ("metric_kind", "operand_scale", "reported_unit", "operation"))
    if not all(isinstance(value, str) for value in (kind, scale, unit, op)):
        return {"passed": False, "issue": "calculation_schema"}
    quote = calc["reported_quote"]
    if (not isinstance(quote, str) or not quote.strip() or quote not in (claim_text or evidence_text)
            or not _number_present(calc["reported"], quote)):
        return {"passed": False, "issue": "calculation_reported_quote"}
    # A deliberately small lexical check catches explicit unit substitution.
    # Ambiguous 'points' still needs the semantic metric definition, not guessing.
    literal_unit = ("percentage_point" if re.search(r"百分点|percentage[ -]points?", quote, re.I) else
                    "percent" if re.search(r"[%％]|\bpercent\b", quote, re.I) else
                    "score_point" if re.search(r"分数点|score[ -]points?", quote, re.I) else None)
    if literal_unit and unit != literal_unit:
        return {"passed": False, "issue": "calculation_reported_unit_substituted", "literal_unit": literal_unit}
    if kind not in {"score", "proportion", "quantity"} or scale not in {"native", "percent", "fraction"}:
        return {"passed": False, "issue": "calculation_unknown_quantity"}
    if (kind == "proportion") != (scale in {"percent", "fraction"}):
        return {"passed": False, "issue": "calculation_scale_mismatch"}
    with localcontext() as ctx:
        ctx.prec = 60
        baseline, value, reported = (Decimal(calc[k]) for k in ("baseline", "value", "reported"))
        if op == "difference":
            expected = value - baseline
            expected_unit = {"score": "score_point", "proportion": "percentage_point", "quantity": "native"}[kind]
            if scale == "fraction":
                expected *= 100
        elif op in {"ratio", "relative_change", "percent_of"}:
            if baseline == 0:
                return {"passed": False, "issue": "calculation_zero_baseline"}
            expected = (value / baseline if op == "ratio" else value / baseline * 100
                        if op == "percent_of" else (value - baseline) / baseline * 100)
            expected_unit = "ratio" if op == "ratio" else "percent"
        else:
            return {"passed": False, "issue": "calculation_unknown_operation"}
        tolerance = Decimal(1).scaleb(reported.as_tuple().exponent) / 2
        issue = ("calculation_unit_mismatch" if unit != expected_unit else
                 "calculation_value_mismatch" if abs(reported - expected) > tolerance else None)
        return {"passed": issue is None, "issue": issue, "expected": str(expected),
                "expected_unit": expected_unit, "reported": str(reported), "reported_unit": unit,
                "rounding_tolerance": str(tolerance),
                "limits": "Arithmetic conditional on cited operands and semantic metric classification."}


def verify_claims(anchors, passages, candidate, assessment):
    """Require coverage and valid citations; never pretend citations prove entailment."""
    if not isinstance(assessment, dict) or set(assessment) != {"checks", "claims"} or not isinstance(assessment["claims"], list):
        return {"passed": False, "issue": "claim_assessment_schema", "items": []}
    expected = {a["id"]: a for a in anchors}
    sources = {p["id"]: p["text"] for p in passages}
    sources.update(candidate)
    seen, receipts = set(), []
    keys = {"anchor_id", "status", "evidence", "reason", "counterexample", "calculations", "no_calculation_reason"}
    for claim in assessment["claims"]:
        if (not isinstance(claim, dict) or set(claim) != keys or not isinstance(claim["anchor_id"], str)
                or claim["anchor_id"] not in expected or claim["anchor_id"] in seen
                or not isinstance(claim["status"], str) or claim["status"] not in {"supported", "unsupported", "uncertain", "not_claim"}
                or not all(isinstance(claim[k], str) for k in ("reason", "counterexample", "no_calculation_reason"))
                or not claim["reason"].strip() or not isinstance(claim["evidence"], list)
                or not isinstance(claim["calculations"], list)):
            return {"passed": False, "issue": "claim_invalid_item", "items": receipts}
        seen.add(claim["anchor_id"])
        anchor = expected[claim["anchor_id"]]
        citations = []
        for evidence in claim["evidence"]:
            if (not isinstance(evidence, dict) or set(evidence) != {"source", "quote"}
                    or not isinstance(evidence["source"], str) or evidence["source"] not in sources
                    or not isinstance(evidence["quote"], str) or not evidence["quote"].strip()
                    or evidence["quote"] not in sources[evidence["source"]]):
                return {"passed": False, "issue": "claim_unbound_evidence", "items": receipts}
            citations.append(evidence["quote"])
        if claim["status"] == "supported" and not citations:
            return {"passed": False, "issue": "claim_missing_evidence", "items": receipts}
        if anchor["scope"] and not claim["counterexample"].strip():
            return {"passed": False, "issue": "claim_missing_scope_test", "items": receipts}
        if anchor["numeric"] and not claim["calculations"] and not claim["no_calculation_reason"].strip():
            return {"passed": False, "issue": "claim_missing_calculation_decision", "items": receipts}
        calculations = [verify_calculation(c, anchor["quote"] + "\n" + "\n".join(citations), anchor["quote"]) for c in claim["calculations"]]
        receipts.append({"anchor_id": anchor["id"], "status": claim["status"], "calculations": calculations,
                         "passed": claim["status"] in {"supported", "not_claim"} and all(c["passed"] for c in calculations)})
    if seen != set(expected):
        return {"passed": False, "issue": "claim_coverage_missing", "missing": sorted(set(expected)-seen), "items": receipts}
    passed = all(r["passed"] for r in receipts)
    return {"passed": passed, "issue": None if passed else "claim_not_supported", "items": receipts}


def assessment_contract_issue(rules, anchors, passages, candidate, assessment):
    """Identify repairable audit syntax/evidence defects, never soften a bad claim."""
    from .reasoning_checklist import checklist_evidence_issue
    checks = {"checks": assessment.get("checks")} if isinstance(assessment, dict) else assessment
    issue = checklist_evidence_issue(rules, candidate, checks)
    receipt = verify_claims(anchors, passages, candidate, assessment)
    if isinstance(assessment, dict):
        # A genuine negative verdict goes to candidate repair, not repeated
        # judge sampling until a preferred verdict appears.
        checks = assessment.get("checks")
        claims = assessment.get("claims")
        if isinstance(checks, list) and any(isinstance(c, dict) and c.get("status") == "unmet" for c in checks):
            return None
        if isinstance(claims, list) and any(isinstance(c, dict) and c.get("status") in ("unsupported", "uncertain") for c in claims):
            return None
    if issue:
        return issue
    if receipt["issue"] not in {None, "claim_not_supported"}:
        return receipt["issue"]
    for item in receipt["items"]:
        for calculation in item["calculations"]:
            if calculation.get("issue") in {"calculation_schema", "calculation_decimal",
                    "calculation_reported_quote", "calculation_reported_unit_substituted"}:
                return calculation["issue"]
    # A missing citation is not evidence that the author's arithmetic is
    # wrong. Repair only when the exact same typed calculation verifies with
    # the already supplied passages; never invent an operand or change a unit.
    known_anchors = {a["id"]: a for a in anchors}
    supplied_text = "\n".join(p["text"] for p in passages)
    if isinstance(assessment, dict) and isinstance(assessment.get("claims"), list):
        for claim in assessment["claims"]:
            if not isinstance(claim, dict) or not isinstance(claim.get("anchor_id"), str):
                continue
            anchor = known_anchors.get(claim["anchor_id"])
            if not anchor or not isinstance(claim.get("calculations"), list):
                continue
            item = next((r for r in receipt["items"] if r["anchor_id"] == anchor["id"]), None)
            if not item:
                continue
            for calc, checked in zip(claim["calculations"], item["calculations"]):
                if (checked.get("issue") == "calculation_uncited_operand"
                        and verify_calculation(calc, anchor["quote"] + "\n" + supplied_text,
                                               anchor["quote"])["passed"]):
                    return "calculation_uncited_operand"
    return None


def assessment_contract_details(rules, anchors, passages, candidate, assessment):
    """Locate every repair target using the same strict validators; no verdict edits."""
    from .reasoning_checklist import checklist_evidence_issue
    checks = assessment.get("checks", []) if isinstance(assessment, dict) else []
    claims = assessment.get("claims", []) if isinstance(assessment, dict) else []
    checks = checks if isinstance(checks, list) else []
    claims = claims if isinstance(claims, list) else []
    rule_ids = {c.get("rule_id") for c in checks if isinstance(c, dict) and isinstance(c.get("rule_id"), str)}
    anchor_ids = {c.get("anchor_id") for c in claims if isinstance(c, dict) and isinstance(c.get("anchor_id"), str)}
    details = {
        "required_rule_ids": [r["id"] for r in rules],
        "required_anchor_ids": [a["id"] for a in anchors],
        "missing_rule_ids": [r["id"] for r in rules if r["id"] not in rule_ids],
        "missing_anchor_ids": [a["id"] for a in anchors if a["id"] not in anchor_ids],
        "rule_errors": [], "claim_errors": [],
    }
    for rule in rules:
        matches = [c for c in checks if isinstance(c, dict) and c.get("rule_id") == rule["id"]]
        issue = checklist_evidence_issue([rule], candidate, {"checks": matches})
        if issue:
            details["rule_errors"].append({"rule_id": rule["id"], "issue": issue})
    for anchor in anchors:
        matches = [c for c in claims if isinstance(c, dict) and c.get("anchor_id") == anchor["id"]]
        receipt = verify_claims([anchor], passages, candidate, {"checks": [], "claims": matches})
        if not receipt["passed"]:
            details["claim_errors"].append({"anchor_id": anchor["id"], "verification": receipt})
    return details
