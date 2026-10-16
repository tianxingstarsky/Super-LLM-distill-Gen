"""Exact visible style-verdict shapes, with immutable scores and raw evidence."""
from lib.domain.workflow_quality import text_issue


def style_verdict(value, *, maximum_reason_length=None, inspect_reason=False, allow_format_metadata=False):
    """Accept only the contract or one additional visible explanation field.

    The optional explanation is not a score, a replacement reason, or provider
    reasoning_content. Keep the raw model object untouched for checkpoints.
    Callers retain their historical reason bounds and content checks. Only an
    explicit opt-in permits the exact redundant ``type: json_object`` format
    marker. Other fields and values remain invalid, not discarded metadata.
    """
    original = value
    if (allow_format_metadata is True and isinstance(value, dict)
            and value.get("type") == "json_object"):
        value = {key: part for key, part in value.items() if key != "type"}
    fields = {"keep", "adherence", "reason"}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | {"reasoning"})
            or type(value.get("keep")) is not bool or type(value.get("adherence")) is not int
            or not 1 <= value["adherence"] <= 5 or not isinstance(value.get("reason"), str)
            or not value["reason"].strip()
            or (maximum_reason_length is not None and len(value["reason"]) > maximum_reason_length)
            or (inspect_reason and text_issue(value["reason"]))
            or ("reasoning" in value and (not isinstance(value["reasoning"], str) or not value["reasoning"].strip()))):
        raise ValueError("invalid_style_judge_schema")
    if "reasoning" not in value and value is original:
        return value
    return {key: value[key] for key in ("keep", "adherence", "reason")}
