"""Deterministic, verifiable arithmetic problems for GSM8K-style training."""
from __future__ import annotations

import ast
import operator
import re


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv}


def evaluate_integer_expression(expression: str) -> int:
    """Evaluate only bounded integer arithmetic; never execute generated code."""
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("invalid_math_expression") from None

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is int and 0 <= node.value <= 100000:
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.FloorDiv) and right == 0:
                raise ValueError("zero_division")
            value = _OPS[type(node.op)](left, right)
            if abs(value) > 10**9:
                raise ValueError("math_result_out_of_range")
            return value
        raise ValueError("unsupported_math_expression")

    return visit(tree)


def build_gsm8k(theme: str, seed: int) -> dict:
    """Build a solvable word problem using a deterministic arithmetic grammar."""
    phrase = re.sub(r"\s+", " ", (theme or "通用训练").strip())[:80] or "通用训练"
    a, b = 2 + seed % 17, 2 + (seed // 17) % 17
    mode = seed % 4
    if mode == 0:
        expression = f"{a} + {b}"
        story = f"整理“{phrase}”时，上午完成 {a} 项，下午又完成 {b} 项。全天共完成多少项？"
    elif mode == 1:
        expression = f"{a} * {b}"
        story = f"整理“{phrase}”的训练资料时，每组有 {a} 份，一共有 {b} 组。总共有多少份？"
    elif mode == 2:
        expression = f"{a + b} - {b}"
        story = f"“{phrase}”项目准备了 {a + b} 份资料，已经使用 {b} 份。还剩多少份？"
    else:
        expression = f"{a * b} // {b}"
        story = f"“{phrase}”资料共 {a * b} 份，平均分给 {b} 组。每组多少份？"
    result = evaluate_integer_expression(expression)
    answer = f"列式：{expression} = {result}。因此答案是 {result}。 <<{expression}={result}>> #### {result}"
    return {"question": story, "answer": answer, "_expression": expression, "_result": result}


def validate_gsm8k(record: dict) -> bool:
    expression, result = record.get("_expression"), record.get("_result")
    if not isinstance(record.get("question"), str) or not record["question"].strip():
        return False
    if type(result) is not int or evaluate_integer_expression(expression) != result:
        return False
    return f"#### {result}" in record.get("answer", "")
