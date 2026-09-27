"""Deterministic, verifiable arithmetic problems for GSM8K-style training."""
from __future__ import annotations

import ast
import operator
import re


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv}


def evaluate_integer_expression(expression: str) -> int:
    """Evaluate only bounded integer arithmetic; never execute generated code."""
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 1024:
        raise ValueError("invalid_math_expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("invalid_math_expression") from None

    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError("math_expression_too_complex")

    def visit(node, depth=0):
        if depth > 24:
            raise ValueError("math_expression_too_complex")
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) is int and 0 <= node.value <= 100000:
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.FloorDiv) and right == 0:
                raise ValueError("zero_division")
            value = _OPS[type(node.op)](left, right)
            if abs(value) > 10**9:
                raise ValueError("math_result_out_of_range")
            return value
        raise ValueError("unsupported_math_expression")

    return visit(tree)


def build_gsm8k(theme: str, seed: int, *, version: int = 1) -> dict:
    """Build a solvable word problem using a deterministic arithmetic grammar."""
    phrase = re.sub(r"\s+", " ", (theme or "通用训练").strip())[:80] or "通用训练"
    if version == 2:
        return _build_multistep(phrase, seed)
    if version != 1:
        raise ValueError("unsupported_math_generator_version")
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


def _build_multistep(phrase: str, seed: int) -> dict:
    a, b, c = 2 + seed % 97, 2 + (seed // 97) % 89, 1 + (seed // 8633) % 83
    mode = seed % 4
    if mode == 0:
        expressions = [f"{a} * {b}", f"{a} * {b} + {c}"]
        explanations = ["先计算成组资料的总数", "再加上额外资料"]
        story = f"“{phrase}”有 {a} 组资料，每组 {b} 份，另有 {c} 份备用资料。共多少份？"
    elif mode == 1:
        c = min(c, a * b - 1)
        expressions = [f"{a} * {b}", f"{a} * {b} - {c}"]
        explanations = ["先计算准备的资料总数", "再减去已经使用的资料"]
        story = f"“{phrase}”准备了 {a} 组资料，每组 {b} 份，已使用 {c} 份。还剩多少份？"
    elif mode == 2:
        expressions = [f"{a} + {b}", f"({a} + {b}) * {c}"]
        explanations = ["先计算每天上午与下午完成的总数", "再乘以工作的天数"]
        story = f"整理“{phrase}”时，每天上午完成 {a} 项，下午完成 {b} 项，连续工作 {c} 天。共完成多少项？"
    else:
        expressions = [f"{a} * {b}", f"{a} * {b} + {c} * {b}", f"({a} * {b} + {c} * {b}) // {b}"]
        explanations = ["先计算原有资料总数", "再加上新增资料", "最后平均分组"]
        story = f"“{phrase}”原有 {a} 组资料，每组 {b} 份，新增 {c * b} 份。平均分给 {b} 组，每组多少份？"
    steps = [{"explanation": explanation, "expression": expression,
              "result": evaluate_integer_expression(expression)}
             for explanation, expression in zip(explanations, expressions)]
    result = steps[-1]["result"]
    answer = "\n".join(f"{step['explanation']}：<<{step['expression']}={step['result']}>>。"
                       for step in steps) + f"\n#### {result}"
    return {"question": story, "answer": answer, "_expression": expressions[-1],
            "_result": result, "_steps": steps, "_family": mode}


def validate_gsm8k(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    expression, result = record.get("_expression"), record.get("_result")
    if not isinstance(record.get("question"), str) or not record["question"].strip():
        return False
    answer = record.get("answer")
    if type(result) is not int or not isinstance(answer, str) or len(answer) > 16000:
        return False
    try:
        if evaluate_integer_expression(expression) != result:
            return False
        calculations = re.findall(r"<<([^<>]+)=(-?\d+)>>", answer)
        if not calculations or answer.count("<<") != len(calculations) or answer.count(">>") != len(calculations):
            return False
        if any(evaluate_integer_expression(formula) != int(value) for formula, value in calculations):
            return False
        # Equal numeric results alone do not establish the same calculation.
        def structure(formula):
            return ast.dump(ast.parse(formula.strip(), mode="eval"), include_attributes=False)

        if structure(calculations[-1][0]) != structure(expression):
            return False
        if "_steps" in record:
            steps = record["_steps"]
            if not isinstance(steps, list) or not steps or len(steps) != len(calculations):
                return False
            for step, (formula, value) in zip(steps, calculations):
                if (not isinstance(step, dict) or type(step.get("result")) is not int
                        or not isinstance(step.get("expression"), str)
                        or not isinstance(step.get("explanation"), str)
                        or not step["explanation"].strip()
                        or evaluate_integer_expression(step["expression"]) != step["result"]
                        or step["result"] != int(value)
                        or structure(step["expression"]) != structure(formula)):
                    return False
        final = re.search(r"####\s*(-?\d+)\s*$", answer)
        return bool(final and answer.count("####") == 1 and int(final[1]) == result
                    and int(calculations[-1][1]) == result)
    except (ValueError, TypeError, SyntaxError, OverflowError, RecursionError):
        return False


def validate_math_candidate(row: dict) -> bool:
    """Recheck persisted candidate evidence before creating training exports."""
    sample = {"question": row.get("question"), "answer": row.get("answer"),
              "_expression": row.get("arithmetic_expression"), "_result": row.get("verified_result")}
    version = row.get("generator_version", 1)
    if type(version) is not int or version not in {1, 2}:
        return False
    if version == 2 or row.get("calculation_steps"):
        sample["_steps"] = row.get("calculation_steps")
    return validate_gsm8k(sample)
