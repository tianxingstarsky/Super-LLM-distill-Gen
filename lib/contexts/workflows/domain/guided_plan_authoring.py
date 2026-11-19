"""Resolve dataset-author annotations to reviewable arithmetic plans using original text."""

from .guided_answers import validate_source
from .guided_calculation_plan import PLAN_SCHEMA, annotated_calculations, calculation_plan_digest, check_calculation_steps


def _unique_span(text, quote):
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("quote must be nonempty text")
    start = text.find(quote)
    if start < 0:
        raise ValueError(f"quote not found: {quote!r}")
    if text.find(quote, start + 1) >= 0:
        raise ValueError(f"ambiguous quote {quote!r}; supply unique surrounding text in 'within'")
    return [start, start + len(quote)]


def _quote_span(text, selector):
    if isinstance(selector, str):
        return _unique_span(text, selector)
    if not isinstance(selector, dict) or set(selector) != {"text", "within"}:
        raise ValueError("select a quote with text or {text, within}")
    outer, _ = _unique_span(text, selector["within"])
    start, end = _unique_span(selector["within"], selector["text"])
    return [outer + start, outer + end]


def _equation_span(proof, selector, equations):
    if isinstance(selector, str):
        matches = [span for equation, span in equations if equation == selector]
        if len(matches) != 1:
            raise ValueError(f"expected one complete annotated equation {selector!r}, found {len(matches)}")
        return matches[0]
    span = _quote_span(proof, selector)
    if not any(span == original for _, original in equations):
        raise ValueError("equation selector must identify a complete source annotation")
    return span


def _bind_input(question, operand):
    if not isinstance(operand, dict):
        raise ValueError("expected an operand source object")
    if set(operand) in ({"question_quote"}, {"question_quote", "interpretation"}):
        bound = {"question_span": _quote_span(question, operand["question_quote"])}
        if "interpretation" in operand:
            bound["interpretation"] = operand["interpretation"]
        return bound
    if set(operand) in ({"constant"}, {"step_id"}):
        return operand.copy()
    raise ValueError("use a question quote, previous step ID, or named conversion for each operand")


def prepare_calculation_plan_candidate(source, annotations):
    """Build a fingerprinted candidate. Trust in its semantic mapping is a separate decision."""
    checked = validate_source(source)
    if "verified_calculation_plan" in source:
        raise ValueError("source already contains a calculation plan")
    question, proof = checked["question"], checked["source_reasoning"]
    equations = annotated_calculations(proof)
    if not isinstance(annotations, list) or not annotations or not equations:
        raise ValueError("need calculation_steps and complete <<expression=value>> source annotations")
    steps = []
    for index, row in enumerate(annotations):
        try:
            fields = {"id", "quantity", "unit", "question_quote", "inputs"}
            if (not isinstance(row, dict) or set(row) not in (fields, fields | {"equation"})
                    or not isinstance(row["inputs"], list)):
                raise ValueError("expected id, quantity, unit, question_quote and inputs")
            if "equation" in row:
                span = _equation_span(proof, row["equation"], equations)
            elif len(annotations) == len(equations):
                span = equations[index][1]
            else:
                raise ValueError("give an explicit equation when steps and source annotations are not one-to-one")
            steps.append({"id": row["id"], "quantity": row["quantity"], "unit": row["unit"],
                          "question_span": _quote_span(question, row["question_quote"]), "equation_span": span,
                          "inputs": [_bind_input(question, item) for item in row["inputs"]]})
        except ValueError as exc:
            raise ValueError(f"step {index + 1}: {exc}") from exc
    check_calculation_steps(checked, steps)
    plan = {"schema": PLAN_SCHEMA, "receipt_id": calculation_plan_digest(checked, steps), "steps": steps}
    return {**source, "verified_calculation_plan": plan}
