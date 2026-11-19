"""Externally attested, source-bound arithmetic operations for guided tutoring."""

import ast
from fractions import Fraction
import re

from .experience_evolution import digest
from .guided_answers import contains_chat_control, number, validate_public_guidance
from .guided_student_calculations import verified_equation_result


PLAN_SCHEMA = "guided-calculation-plan/v1"
_FORMULA = re.compile(r"<<([^<>\n]{1,220})>>")
_COUNTS = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split(), range(21)))
_FRACTIONS = {"half": Fraction(1, 2), "a half": Fraction(1, 2), "one half": Fraction(1, 2),
              "quarter": Fraction(1, 4), "a quarter": Fraction(1, 4), "one quarter": Fraction(1, 4),
              "one fourth": Fraction(1, 4), "one third": Fraction(1, 3)}
_CONVERSIONS = {"days_per_week": (7, r"\bweeks?\b|星期|周|週"),
                "items_per_dozen": (12, r"\bdozens?\b|打")}


def calculation_plan_digest(source, steps):
    """Fingerprint an independently checked assignment of quantities and dependencies."""
    return digest({
        "schema": PLAN_SCHEMA,
        "source": {k: source.get(k) for k in (
            "id", "question", "task_system", "answer_requirement", "direct_answer_syntax", "gold", "source_reasoning")},
        "provenance": source.get("source", {}),
        "steps": steps,
    })


def _span(text, span):
    if (not isinstance(span, list) or len(span) != 2 or any(type(i) is not int for i in span)
            or not 0 <= span[0] < span[1] <= len(text) or not text[span[0]:span[1]].strip()):
        raise ValueError("invalid source span in calculation plan")
    return text[span[0]:span[1]]


def calculation_for_step(source, step):
    """Return the independently recomputed expression and exact result for a validated step."""
    equation = _span(source["source_reasoning"], step["equation_span"])
    result = verified_equation_result(equation)
    if result is None:
        raise ValueError("source calculation does not establish its stated result")
    return equation.split("=")[0].strip(), result


def _question_quantity(question, item):
    text = _span(question, item["question_span"])
    if text.strip() != text:
        raise ValueError("calculation operand span must identify an exact quantity")
    start, end = item["question_span"]
    if ((start and text[0].isalnum() and question[start - 1].isascii() and question[start - 1].isalnum())
            or (end < len(question) and text[-1].isalnum() and question[end].isascii() and question[end].isalnum())
            or (start > 1 and question[start - 1] in ".,/" and question[start - 2].isdigit())
            or (end + 1 < len(question) and question[end] in ".,/" and question[end + 1].isdigit())
            or (start and text[0].isdigit() and question[start - 1] in "-−"
                and (start == 1 or not question[start - 2].isdigit()))):
        raise ValueError("calculation operand selects part of a quantity")
    literal = text.lower().replace("-", " ")
    if "interpretation" in item:
        if item["interpretation"] != "denominator" or literal not in _FRACTIONS:
            raise ValueError("unsupported quantity interpretation")
        return _FRACTIONS[literal].denominator
    if literal in _FRACTIONS:
        return _FRACTIONS[literal]
    return _COUNTS[literal] if literal in _COUNTS else number(text)


def _numeric_operands(expression):
    # The calculator has already checked the operators, literals and expression size.
    expression = expression.translate(str.maketrans({"×": "*", "÷": "/", "−": "-"}))
    if not re.fullmatch(r"[\d. +*/\t()\-]+", expression):
        raise ValueError("calculation annotations require literal arithmetic operands")

    def leaves(node):
        if isinstance(node, ast.Constant):
            yield number(ast.get_source_segment(expression, node))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            yield -number(ast.get_source_segment(expression, node.operand))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            yield from leaves(node.operand)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            yield from leaves(node.left)
            yield from leaves(node.right)
        else:
            raise ValueError("unsupported operation in source calculation")

    return list(leaves(ast.parse(expression, mode="eval").body))


def _input_quantity(item, question, results):
    if not isinstance(item, dict):
        raise ValueError("invalid calculation operand binding")
    if set(item) == {"step_id"}:
        if not isinstance(item["step_id"], str) or item["step_id"] not in results:
            raise ValueError("calculation dependency has not been established")
        return results[item["step_id"]]
    if set(item) in ({"question_span"}, {"question_span", "interpretation"}):
        return _question_quantity(question, item)
    if set(item) == {"constant"} and isinstance(item["constant"], str):
        conversion = _CONVERSIONS.get(item["constant"])
        if conversion and re.search(conversion[1], question, re.I):
            return conversion[0]
    raise ValueError("unbound calculation operand or unsupported conversion")


def validate_calculation_plan(source, trusted_receipts):
    plan = source["verified_calculation_plan"]
    if (not isinstance(plan, dict) or set(plan) != {"schema", "receipt_id", "steps"}
            or plan["schema"] != PLAN_SCHEMA or not isinstance(plan["steps"], list) or not plan["steps"]):
        raise ValueError("invalid verified calculation plan")
    if (not isinstance(plan["receipt_id"], str) or not trusted_receipts
            or plan["receipt_id"] not in trusted_receipts
            or plan["receipt_id"] != calculation_plan_digest(source, plan["steps"])):
        raise ValueError("calculation plan needs its own independently trusted source receipt")
    check_calculation_steps(source, plan["steps"])
    return plan


def check_calculation_steps(source, steps):
    """Check source bindings, arithmetic and dependency closure independently of authorization."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("calculation plan needs at least one source operation")
    key = source.get("gold") or {}
    if key.get("kind") != "number" or not source.get("source_reasoning"):
        raise ValueError("verified calculations need an independent numerical key and worked source")
    proof, question = source["source_reasoning"], source["question"]
    annotations = {tuple(span) for _, span in annotated_calculations(proof)}
    seen_equations, results, used_parents = set(), {}, set()
    for step in steps:
        if (not isinstance(step, dict) or set(step) != {
                "id", "quantity", "unit", "question_span", "equation_span", "inputs"}
                or not isinstance(step["id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z_0-9.-]*", step["id"])
                or step["id"] in results or not isinstance(step["inputs"], list)):
            raise ValueError("invalid or duplicate calculation step")
        quote = _span(question, step["question_span"])
        for name in ("quantity", "unit"):
            label = step[name]
            if (not isinstance(label, str) or not label.strip() or len(label) > 160
                    or not re.fullmatch(r"[^\W\d_]+(?:[ /'’-][^\W\d_]+)*", label)):
                raise ValueError("calculation quantity and unit must be descriptive labels without values")
            validate_public_guidance(label, key)
        if contains_chat_control(quote):
            raise ValueError("calculation evidence contains a chat delimiter")
        _span(proof, step["equation_span"])
        span = tuple(step["equation_span"])
        if span not in annotations or span in seen_equations:
            raise ValueError("calculation must cite a distinct complete annotated source equation")
        expression, value = calculation_for_step(source, step)
        operands = [_input_quantity(item, question, results) for item in step["inputs"]]
        if operands != _numeric_operands(expression):
            raise ValueError("calculation operands differ from their question or prior-step evidence")
        used_parents.update(item["step_id"] for item in step["inputs"] if "step_id" in item)
        seen_equations.add(span)
        results[step["id"]] = value
    terminal = steps[-1]["id"]
    if set(results) - used_parents != {terminal} or results[terminal] != number(key["value"]):
        raise ValueError("verified calculations must form a closed derivation to the independent answer key")


def annotated_calculations(source_reasoning):
    """Yield each entire annotated equation and its original span, in source order."""
    return [(m[1], list(m.span(1))) for m in _FORMULA.finditer(source_reasoning)]


def next_calculation_step(source, verified_ids):
    """Choose the earliest still missing operation whose declared prerequisites are verified."""
    verified_ids = set(verified_ids)
    consumed, candidate = set(), None
    for step in source["verified_calculation_plan"]["steps"]:
        available = all(item["step_id"] in consumed for item in step["inputs"] if "step_id" in item)
        if step["id"] in verified_ids:
            if not available:
                raise ValueError("verified calculation history is missing a prerequisite")
            consumed.add(step["id"])
        elif available and candidate is None:
            candidate = step
    if consumed != verified_ids:
        raise ValueError("verified calculation history contains an unknown step")
    return candidate


def public_calculation_step(source, step, verified_ids):
    """Expose the operand expression and the task premise, keeping its expected result private."""
    requires = list(dict.fromkeys(item["step_id"] for item in step["inputs"] if "step_id" in item))
    frontier = next_calculation_step(source, verified_ids)
    if frontier != step:
        raise ValueError("operation is not the current certified calculation step")
    expression, _ = calculation_for_step(source, step)
    return {"step_id": step["id"], "quantity": step["quantity"], "unit": step["unit"],
            "question_quote": _span(source["question"], step["question_span"]), "question_span": step["question_span"],
            "operation": expression, "requires": requires, "is_final": step == source["verified_calculation_plan"]["steps"][-1]}
