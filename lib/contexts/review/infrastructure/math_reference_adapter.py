"""Math-Verify in a bounded worker, including on Windows/worker threads."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class MathVerifyReferenceAdapter:
    def __init__(self, timeout_seconds=15):
        self.timeout_seconds = timeout_seconds

    def compare(self, expected, candidate):
        # Math-Verify can strip units and prose. This narrow route deliberately
        # accepts only whole numeric expressions, never arbitrary science text.
        if not all(_numeric_surface(s) for s in (expected, candidate)):
            return {"status": "unresolved", "reason": "not_a_unitless_numeric_expression"}
        try:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", __name__],
                input=json.dumps([expected, candidate]), capture_output=True, text=True, encoding="utf-8",
                cwd=Path(__file__).resolve().parents[4], timeout=self.timeout_seconds,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode != 0:
                return {"status": "unresolved", "reason": "math_verifier_worker_failed"}
            return json.loads(completed.stdout)
        except subprocess.TimeoutExpired:
            return {"status": "unresolved", "reason": "math_verifier_timeout"}
        except (OSError, ValueError):
            return {"status": "unresolved", "reason": "math_verifier_unavailable"}


def _numeric_surface(text):
    # A routing guard, not a second mathematical parser. Unsupported notation
    # stays unresolved; the mature parser owns expression interpretation.
    text = _without_math_delimiters(text)
    if "$" in text or re.search(r"\d\s+\d", text):
        return False
    commands = re.findall(r"\\([A-Za-z]+)", text)
    if any(c not in {"frac", "dfrac", "tfrac", "sqrt", "pi", "left", "right", "cdot", "times"}
           for c in commands):
        return False
    remainder = re.sub(r"\\[A-Za-z]+", "", text)
    return bool(re.fullmatch(r"[0-9\s.{}()\[\]+*/^=%\\\-]+", remainder))


def _without_math_delimiters(text):
    text = text.strip()
    for delimiter in ("$$", "$"):
        if text.startswith(delimiter) and text.endswith(delimiter) and len(text) > len(delimiter) * 2:
            return text[len(delimiter):-len(delimiter)]
    return text


def _compare_locally(expected, candidate):
    from importlib.metadata import version
    from math_verify import verify
    from latex2sympy2_extended import latex2sympy, NormalizationConfig
    from sympy import Expr
    receipt = {"verifier": "math-verify", "verifier_version": version("math-verify")}
    parsed = []
    for text in (expected, candidate):
        # An outer process timeout covers import, parsing AND verification.
        # Avoid the SDK's nested process target on Windows and signal alarms in
        # application threads. No global monkeypatch and no unbounded host work.
        # Use the SDK's parser dependency directly for a whole expression.
        # Its prose extractor intentionally selects a final answer span, which
        # would ignore conflicting earlier spans in this stricter contract.
        expression = latex2sympy(_without_math_delimiters(text), normalization_config=NormalizationConfig(
            basic_latex=True, units=False, malformed_operators=False, nits=False, boxed="none", equations=False))
        if (not isinstance(expression, Expr)
                or expression.is_number is not True or expression.is_finite is not True):
            return {**receipt, "status": "unresolved", "reason": "expression_not_fully_resolved"}
        parsed.append(expression)
    equal = verify(parsed[0], parsed[1], strict=True, float_rounding=50, numeric_precision=50,
                   timeout_seconds=None, raise_on_error=True)
    return {**receipt, "status": "matched" if equal else "mismatched",
            "reason": "numeric_equivalence" if equal else "numeric_inequality",
            "parsed_expected": str(parsed[0]), "parsed_candidate": str(parsed[1])}


if __name__ == "__main__":
    try:
        pair = json.loads(sys.stdin.read(10000))
        result = _compare_locally(*pair)
    except Exception:
        result = {"status": "unresolved", "reason": "math_parser_or_verifier_error"}
    print(json.dumps(result))
