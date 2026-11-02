"""Submit one review and both guidance scopes through existing strict tools."""
from copy import deepcopy
import json

from lib.contexts.review.contracts import (
    review_evolution_submission_schema, parse_review_evolution_submission,
)
from lib.contexts.models.contracts import ModelJSONError
from lib.domain.workflow_quality import canonical

SUBMIT_REVIEW_EVOLUTION = "submit_review_evolution"
SUBMISSION_INSTRUCTION = (
    "提交时使用 submit_review_evolution 的扁平参数合同。质量判断和各维度分数直接写入工具参数顶层；"
    "memory_status 与 memory_items 也在顶层。程序会构造 verdict、scores 和 memory_proposal，"
    "不要自行增加这些嵌套对象，不要重复分数或判断字段。这里只规定传输格式，不改变质量标准。"
)


def request_review_evolution(runtime, key, role, payload):
    tool = {"type": "function", "function": {"name": SUBMIT_REVIEW_EVOLUTION,
        "description": "Submit the current draft review, temporary task instructions and optional reusable experience together.",
        "strict": True, "parameters": review_evolution_submission_schema()}}
    prepared = [{"role": "system", "content": runtime.prompt_text("workflow.review_score", stage="review")
                 + "\n\n" + SUBMISSION_INSTRUCTION},
                {"role": "user", "content": canonical(payload)}]
    from lib.bootstrap.review_evolution import review_evolution_tool_transport
    try:
        response = review_evolution_tool_transport(runtime).prepared_tool_turn(key, role, prepared, [tool],
            model_stage="review", strict=True, capture_details=True)
    except ValueError as error:
        details = getattr(error, "response_details", {})
        if str(error) != "invalid_model_tool_arguments" or not details.get("incomplete_response"):
            raise
        invalid = ModelJSONError(1)
        invalid.response_details = deepcopy(details)
        raise invalid from error
    try:
        assistant = response["assistant"]
        calls = assistant.get("tool_calls", [])
        if (len(calls) != 1 or calls[0].get("function", {}).get("name") != SUBMIT_REVIEW_EVOLUTION
                or assistant.get("content", "").strip()):
            raise ValueError("review_evolution_submission_invalid")
        raw = json.loads(calls[0]["function"]["arguments"])
        parsed = parse_review_evolution_submission(raw)
    except (KeyError, TypeError, ValueError) as error:
        raise ModelJSONError(1) from error
    return parsed, deepcopy(raw), deepcopy(response.get("response_details", {}))
