"""Recover a student's explicit arithmetic without certifying their surrounding prose."""

import ast
import operator
import re

from .experience_evolution import digest
from .guided_answers import contains_chat_control, number


_UNIT = r"\\(?:text|mathrm)\{\s*\(?[A-Za-z]{1,16}(?:/[A-Za-z]{1,16})?\)?\s*\}"
_TEX_OPERATOR = {r"\times": "*", r"\cdot": "*", r"\div": "/"}
_SYMBOLS = str.maketrans({"×": "*", "÷": "/", "−": "-"})
_MATH_RUN = re.compile(rf"(?:{_UNIT}|\\(?:times|cdot|div)\b|[\d \t.()+×÷−*/=\-])+")
_ANNOTATION = re.compile(r"<<([^<>\n]{1,220})>>")
_NONASSERTION = re.compile(
    r"\b(?:if|suppos\w*|assum\w*|hypothetic\w*|guess\w*|maybe|perhaps|possibly|could|would|might|"
    r"unless|not|never|wrong|incorrect|false|mistak\w*|invalid|instead|rather than|reject\w*)\b"
    r"|n['’]t\b|[?？]|假设|如果|若|猜测|也许|可能|错误|不对|不是|不能|并非|未必", re.I,
)
_OPERATIONS = {ast.Add: ("+", operator.add), ast.Sub: ("-", operator.sub),
               ast.Mult: ("*", operator.mul), ast.Div: ("/", operator.truediv)}


def _checked_equation(literal):
    if len(literal) > 384:
        return None
    math = re.sub(_UNIT, " ", literal).translate(_SYMBOLS)
    for latex, op in _TEX_OPERATOR.items():
        math = math.replace(latex, op)
    left, equals, right = math.partition("=")
    left, right = left.strip(), right.strip()
    answer = number(right)
    if (not equals or answer is None or len(left) > 160
            or not re.fullmatch(r"[0-9.() +*/\t\-]+", left)):
        return None

    def calculate(node):
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            value = number(ast.get_source_segment(left, node))
            if value is not None:
                return value, ("number", str(value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value, key = calculate(node.operand)
            return -value, ("negate", key)
        if not isinstance(node, ast.BinOp) or type(node.op) not in _OPERATIONS:
            raise ValueError
        op, apply = _OPERATIONS[type(node.op)]
        a, first = calculate(node.left)
        b, second = calculate(node.right)
        if op in {"+", "*"} and second < first:
            first, second = second, first
        return apply(a, b), (op, first, second)

    try:
        tree = ast.parse(left, mode="eval")
        if not isinstance(tree.body, ast.BinOp) or sum(1 for _ in ast.walk(tree)) > 48:
            return None
        result, key = calculate(tree.body)
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None
    if result == answer:
        return key, answer, f"{' '.join(left.split())} = {right}"
    return None


def _student_equations(text):
    # Match a complete numeric island. Never start inside another expression,
    # a LaTeX command, or an alphanumeric token.
    for run in _MATH_RUN.finditer(text):
        expr = run.group().strip().removesuffix(".").rstrip()
        if "=" not in expr:
            continue
        start = text.index(expr, run.start(), run.end())
        if expr.startswith("=") and text[:start].rstrip()[-1:].isalpha():
            after_label = expr[1:].lstrip()
            start += len(expr) - len(after_label)
            expr = after_label
        if not expr:
            continue
        end = start + len(expr)
        previous = text[start - 1:start] if start else ""
        following = text[end:end + 1]
        if (any(ch.isalnum() for ch in (previous, following))
                or (previous == "," and text[start - 2:start - 1].isdigit())
                or (following == "," and text[end + 1:end + 2].isdigit())
                or any(ch in r"\^%<>!{}_" for ch in (text[:start].rstrip()[-1:], text[end:].lstrip()[:1]) if ch)):
            continue
        equation = _checked_equation(expr)
        if equation:
            yield equation, expr, start


_SOURCE_QUANTITY = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s+(?P<unit>[a-z][a-z-]{1,24})\b", re.ASCII)
_PROSE_UNIT = re.compile(r"[ \t]*\$*[ \t]+(?P<unit>[A-Za-z][A-Za-z-]{1,24})\b")
_UNIT_QUALIFIER = re.compile(r"\s*(?:/|\b(?:per|squared|cubed|as|times|each|apiece|for (?:each|every))\b)", re.I)


def _paired_result_unit(source, annotation, pieces, index, at, quote):
    """Bind the unit printed alongside the source result to a local student quantity or equation label."""
    reasoning = source["source_reasoning"]
    supplied = _SOURCE_QUANTITY.match(reasoning, annotation.end())
    if not supplied:
        return None
    amount, unit = supplied.group("amount", "unit")
    rhs = quote.rsplit("=", 1)[-1].strip()
    if (amount != rhs or not re.search(r"\b" + re.escape(unit) + r"\b", source["question"], re.I)
            or unit in {"times", "plus", "minus", "more", "less", "extra", "square", "cubic",
                        "is", "was", "does", "has", "equals", "means", "gives", "leaves", "remains"}
            or not (unit.endswith("s") or unit in {"feet", "mice", "kg", "km", "cm", "ml"})
            or _UNIT_QUALIFIER.match(reasoning, supplied.end())):
        return None
    piece = pieces[index]
    text = piece["text"]
    end = at + len(quote)
    amount_start, amount_end = end - len(rhs), end
    suffix = _PROSE_UNIT.match(text, end)
    if suffix:
        if suffix["unit"].casefold() != unit or _UNIT_QUALIFIER.match(text, suffix.end()):
            return None
        unit_start, unit_end = suffix.span("unit")
    else:
        label = re.search(r"\b(?P<unit>" + re.escape(unit) + r")[ \t]*[=:][ \t]*\$*$", text[:at], re.I)
        if label:
            unit_start, unit_end = label.span("unit")
            if re.search(r"(?:/|\b(?:per|square|cubic))\s*$", text[:unit_start], re.I):
                return None
        elif index and (label := re.fullmatch(r"\s*([A-Za-z][A-Za-z ]{0,48}?)\s*=\s*\$*", text[:at])):
            # Accept “Green = 6 rooms. Paint Green = (3/5) ...” only with a matching local label.
            previous = pieces[index - 1]
            evidence = re.fullmatch(
                r"\s*(?P<label>[A-Za-z][A-Za-z ]{0,48}?)\s*=\s*\$?(?P<amount>" + re.escape(amount)
                + r")\$?\s+(?P<unit>" + re.escape(unit) + r")[.!]?\s*", previous["text"], re.I)
            if (not evidence or previous["author"] != "student" or previous["end"] != piece["start"]
                    or _NONASSERTION.search(previous["text"])
                    or not (" " + label[1].strip().casefold()).endswith(" " + evidence["label"].strip().casefold())):
                return None
            piece, text = previous, previous["text"]
            amount_start, amount_end = evidence.span("amount")
            unit_start, unit_end = evidence.span("unit")
        else:
            return None
    start, stop = min(unit_start, amount_start), max(unit_end, amount_end)
    offset = piece["start"]
    return {"amount_text": amount, "unit": unit, "text": f"{amount} {unit}", "student_segment_id": piece["id"],
            "student_quote": text[start:stop], "student_span": [offset + start, offset + stop],
            "student_amount_span": [offset + amount_start, offset + amount_end],
            "student_unit_span": [offset + unit_start, offset + unit_end],
            "source_quote": supplied.group(), "source_span": list(supplied.span())}


def recovered_student_calculations(source, pieces, *, prior_hints=(), pair_result_units=False):
    """Return one authenticated arithmetic finding per student segment for protocol 4.8.2."""
    gold = source.get("gold") or {}
    answer = number(gold.get("value")) if gold.get("kind") == "number" else None
    if answer is None:
        return {}
    reasoning = source.get("source_reasoning") or ""
    supplied = {value[0] for value, _, _ in _student_equations(source.get("question", ""))}
    for hint in prior_hints:
        supplied.update(value[0] for value, _, _ in _student_equations(hint))
    annotations = {}
    for match in _ANNOTATION.finditer(reasoning):
        equation = _checked_equation(match[1])
        if equation and equation[1] != answer and equation[0] not in supplied:
            annotations.setdefault(equation[0], match)
    found = {}
    for index, piece in enumerate(pieces):
        if not annotations:
            break
        text, offset = piece["text"], piece["start"]
        if (piece["author"] != "student" or offset < 0 or piece["end"] - offset != len(text)
                or contains_chat_control(text) or _NONASSERTION.search(text)):
            continue
        if index:
            lead_in = pieces[index - 1]["text"].rstrip()
            if lead_in.endswith((":", "：", ",", "，")) and _NONASSERTION.search(lead_in):
                continue
        for (key, _, statement), quote, at in _student_equations(text):
            annotation = annotations.pop(key, None)
            if annotation is None:
                continue
            sid = piece["id"]
            found[sid] = {
                "segment_id": sid, "author": "student", "text": statement,
                "source_span": [offset + at, offset + at + len(quote)], "student_quote": quote,
                "source_calculation_quote": annotation.group(), "source_calculation_span": list(annotation.span()),
                "basis_id": "arithmetic", "depends_on": [], "calculation_key": digest(key),
                "evidence_level": "source_verified_student_calculation", "faithfulness_checked": True,
            }
            if pair_result_units:
                quantity = _paired_result_unit(source, annotation, pieces, index, at, quote)
                if quantity:
                    found[sid]["quantity_unit"] = quantity
                    found[sid]["text"] += " " + quantity["unit"]
            break
    return found
