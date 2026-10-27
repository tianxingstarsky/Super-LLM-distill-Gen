"""Program-owned submission schema and record projection; no quality exemptions."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from .authored_trace_transport import authored_response_schema

SUBMIT_AUTHORED = "submit_authored_candidate"


def strict_submission_enabled(recipe):
    return type(recipe.get("authored_submission_version")) is int and recipe["authored_submission_version"] == 1


def strict_tool_schema(schema):
    """Project provider grammar only. The full local schema remains mandatory."""
    if isinstance(schema, list):
        return [strict_tool_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = {key: strict_tool_schema(value) for key, value in schema.items()
              if key not in {"minLength", "maxLength", "minItems", "maxItems"}}
    if result.get("type") == "object":
        result["required"] = list(result["properties"])
        result["additionalProperties"] = False
    return result


def authored_submission_tool(*, answer_required):
    return {"type": "function", "function": {"name": SUBMIT_AUTHORED, "strict": True,
        "description": "Submit the complete authored rationale and its concrete evidence trace"
            + (" with the final answer." if answer_required else "; the answer is generated in the next stage.")
            + " Submission is not a quality verdict. Preserve every argument and limitation.",
        "parameters": strict_tool_schema(authored_response_schema(answer_required=answer_required))}}


def submission_instruction(*, answer_required, local_edit=False):
    return ("\n本阶段通过 submit_authored_candidate 工具提交完整新稿；"
            "这是交付通道，不是要求再次评审，不执行外部动作。"
            "前文的 JSON 字段约定在此对应工具参数，不在普通正文手写 JSON 或代码围栏。"
            "完整 reasoning、具体 reasoning_trace" + (" 和 answer" if answer_required else "")
            + "均不可省略；工具提交不能替代推理、证据与内容核查。"
            "字段、类型和转义由工具协议约束，程序负责记录组装。"
            "只提交一次完整结果；不编造检查结论，不补未经证实的条件，不规定推理顺序。"
            + ("\n以上完整提交要求适用于首次交付。随后若真实工具回执明确要求修改已提交稿件，"
               "进入编辑阶段：使用 edit_authored_candidate 提交局部替换及更新后的论证记录，"
               "无需重复输出完整正文；问题、历史与证据仍以原始上下文为准，不能用补条件掩盖错误。"
               if local_edit else ""))


def assemble_authored_submission(assistant, *, answer_required):
    """Consume one complete tool call. Never guess, repair or parse prose."""
    calls = assistant.get("tool_calls", []) if isinstance(assistant, dict) else []
    if len(calls) != 1 or calls[0].get("function", {}).get("name") != SUBMIT_AUTHORED:
        raise ValueError("authored_submission_missing_or_ambiguous")
    if assistant.get("content", "").strip():
        raise ValueError("authored_submission_unassigned_content")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("authored_submission_duplicate_field")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("authored_submission_nonfinite_number")

    payload = json.loads(calls[0]["function"]["arguments"], object_pairs_hook=unique_object,
                         parse_constant=invalid_constant)
    schema = authored_response_schema(answer_required=answer_required)
    # The tool has a closed set of fields even where old joint responses
    # permitted unrelated metadata. Never silently discard extra fields.
    schema["additionalProperties"] = False
    if not Draft202012Validator(schema).is_valid(payload):
        raise ValueError("authored_submission_schema_invalid")
    trace = payload["reasoning_trace"]
    candidate = {"reasoning": payload["reasoning"], "reasoning_trace": {
        "task": trace["task"], "premises": list(trace["premises"]),
        "steps": [{key: step[key] for key in ("basis", "inference", "result")} for step in trace["steps"]],
        "checks": [{key: check[key] for key in ("check", "result")} for check in trace["checks"]],
        "limitations": list(trace["limitations"])}}
    if answer_required:
        candidate["answer"] = payload["answer"]
    return candidate
