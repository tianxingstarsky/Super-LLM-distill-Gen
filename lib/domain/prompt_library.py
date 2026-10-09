"""Literal, bounded personal prompt templates for supported workflow editors."""
from __future__ import annotations

import re

from lib.domain.reasoning_trim import validate_reasoning_trim
from lib.domain.workflow_generation import validate_node_generation
from lib.domain.workflow_node_prompts import NODE_PROMPT_IDS, validate_node_prompts
from lib.domain.workflow_qa_director import validate_qa_director


MAX_TEMPLATE_NAME_CHARS = 80
MAX_TEMPLATE_REVISION = 2**63 - 1
_TEMPLATE_ID = re.compile(r"[a-f0-9]{32}")


def validate_prompt_template_id(identifier: str) -> str:
    if not isinstance(identifier, str) or not _TEMPLATE_ID.fullmatch(identifier):
        raise ValueError("prompt_library_invalid_id")
    return identifier


def validate_prompt_template_name(name: str) -> str:
    if (not isinstance(name, str) or not name.strip()
            or len(name) > MAX_TEMPLATE_NAME_CHARS or "\x00" in name
            or any(ord(character) < 32 for character in name)):
        raise ValueError("prompt_library_invalid_name")
    try:
        name.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("prompt_library_invalid_name") from error
    return name.strip()


def validate_prompt_scope(scope: str) -> str:
    if not isinstance(scope, str):
        raise ValueError("prompt_library_invalid_scope")
    if scope in {"director.rules", "trim.rules", "generation:sft", "generation:cot"}:
        return scope
    if scope.startswith("node:"):
        parts = scope.split(":")
        if len(parts) == 3 and parts[2] in NODE_PROMPT_IDS.get(parts[1], ()):
            return scope
    raise ValueError("prompt_library_invalid_scope")


def validate_prompt_payload(scope: str, payload: dict) -> dict:
    """Validate editor fields without interpolation or whitespace normalization."""
    scope = validate_prompt_scope(scope)
    if not isinstance(payload, dict):
        raise ValueError("prompt_library_invalid_payload")
    try:
        if scope.startswith("node:"):
            _, stage, prompt_id = scope.split(":")
            if set(payload) != {"text"}:
                raise ValueError("invalid_fields")
            validate_node_prompts({stage: {prompt_id: payload["text"]}})
        elif scope == "director.rules":
            if set(payload) != {"question_rules", "answer_rules"}:
                raise ValueError("invalid_fields")
            validate_qa_director({"enabled": True, **payload})
        elif scope.startswith("generation:"):
            stage = scope.split(":")[1]
            if set(payload) != {"style", "instruction"}:
                raise ValueError("invalid_fields")
            validate_node_generation({stage: {"enabled": True, **payload}})
        else:
            if set(payload) != {"template", "instruction", "custom_prompt"}:
                raise ValueError("invalid_fields")
            validate_reasoning_trim({"enabled": True, **payload})
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError("prompt_library_invalid_payload") from error
    return dict(payload)


def validate_prompt_revision(revision: int) -> int:
    if type(revision) is not int or not 1 <= revision <= MAX_TEMPLATE_REVISION:
        raise ValueError("prompt_library_invalid_revision")
    return revision
