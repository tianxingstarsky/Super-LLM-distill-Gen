"""Decode complete model JSON without corrupting fenced document content."""
from __future__ import annotations
import json
from typing import Any, Dict


class JSONResponseDecodeError(ValueError):
    """A syntax failure, distinct from adapter bugs or receipt-save failures."""

    def __init__(self, error: json.JSONDecodeError):
        self.position, self.line, self.column = error.pos, error.lineno, error.colno
        self.reason = error.msg
        super().__init__("JSON 解析失败")


def parse_json_robust(output: str) -> Dict[str, Any]:
    """Decode a complete value before considering an external code fence."""
    text = output.strip()
    try:
        # A document or answer can legitimately contain Markdown code fences.
        # Removing those first would corrupt an otherwise valid JSON response.
        return json.loads(text)
    except json.JSONDecodeError as error:
        last_error = error
    fence = text.find("```")
    first_object = text.find("{")
    if fence >= 0 and (first_object < 0 or fence < first_object):
        # Only unwrap a fence outside the JSON value. Take its closing fence
        # from the end so embedded code blocks remain part of the document.
        end = text.rfind("```")
        if end > fence:
            text = text[fence + 3:end].strip()
        first_line = text.split("\n", 1)[0].strip()
        if first_line and first_line.isalpha() and "\n" in text:
            text = text.split("\n", 1)[1].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        last_error = error
    # Delegate string escaping and nested-object boundaries to CPython's JSON
    # decoder. Legacy prose/fence envelopes remain supported without JSON repair.
    start = text.find("{")
    if start >= 0:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
            return value
        except json.JSONDecodeError as error:
            last_error = error
    raise JSONResponseDecodeError(last_error) from None
