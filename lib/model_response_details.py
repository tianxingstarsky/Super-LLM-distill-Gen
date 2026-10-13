"""Call-local, provider-authored reasoning separate from visible JSON data."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


class ModelJSONResult(dict):
    """Trusted workflow JSON with transport metadata outside model-owned keys."""

    def __init__(self, data, response_details=None):
        super().__init__(data)
        self.response_details = deepcopy(dict(response_details or {}))


def _get(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _joined_text(parts) -> str:
    return "".join(value for value in parts if isinstance(value, str))


def build_response_details(api_format: str, *, reasoning_content: str = "",
                           reasoning_summary: str = "") -> dict:
    """Keep exact provider text; whitespace is used only to detect emptiness."""
    details = {"api_format": api_format}
    for name, value in (("reasoning_content", reasoning_content),
                        ("reasoning_summary", reasoning_summary)):
        if isinstance(value, str) and value.strip():
            details[name] = value
    return details


def extract_response_details(response: Any, api_format: str) -> dict:
    """Read visible reasoning fields without treating opaque blocks as text."""
    reasoning, summary = [], []
    if api_format == "chat":
        choices = _get(response, "choices", ()) or ()
        if choices:
            reasoning.append(_get(_get(choices[0], "message"), "reasoning_content", ""))
    elif api_format == "responses":
        for item in _get(response, "output", ()) or ():
            if _get(item, "type") != "reasoning":
                continue
            for block in _get(item, "content", ()) or ():
                if _get(block, "type") == "reasoning_text":
                    reasoning.append(_get(block, "text", ""))
            for block in _get(item, "summary", ()) or ():
                if _get(block, "type") == "summary_text":
                    summary.append(_get(block, "text", ""))
    elif api_format == "anthropic":
        for block in _get(response, "content", ()) or ():
            if _get(block, "type") == "thinking":
                reasoning.append(_get(block, "thinking", ""))
    return build_response_details(api_format, reasoning_content=_joined_text(reasoning),
                                  reasoning_summary=_joined_text(summary))


def commit_response_details(target: dict | None, details: dict):
    """Publish only a completed request's metadata into its caller-owned map."""
    if target is not None:
        target.clear()
        target.update(details)
