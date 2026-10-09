"""Literal prompt templates bound to individual workflow nodes."""
from __future__ import annotations

from lib.prompts import get, render


MAX_NODE_PROMPT_CHARS = 32_768
NODE_PROMPT_IDS = {
    "ingest": ("workflow.plan", "workflow.document_vision"),
    "cpt": ("workflow.corpus", "workflow.jev_score"),
    "sft": ("workflow.sft", "workflow.sft_styled", "workflow.jev_score", "workflow.style_check"),
    "multiturn": ("workflow.multiturn_user", "workflow.multiturn_assistant",
                  "workflow.jev_score", "workflow.multiturn_consistency"),
    "preference": ("workflow.alternative", "workflow.jev_score"),
    "cot": ("workflow.cot_generate", "workflow.rationale_check", "workflow.style_check"),
    "trim": ("workflow.trim", "workflow.trim_check", "workflow.trim_rules_check"),
}


def builtin_node_prompt(prompt_id: str) -> str:
    """Return editable text, with JSON braces already rendered exactly once."""
    if not any(prompt_id in ids for ids in NODE_PROMPT_IDS.values()):
        raise ValueError("invalid_node_prompt_id")
    return render(get(prompt_id))


def active_node_prompt_ids(stage: str, source_mode: str, *, node_generation=None) -> tuple[str, ...]:
    """Only offer templates that the selected node can actually call."""
    if stage == "ingest":
        return (("workflow.plan",) if source_mode == "开放需求" else
                ("workflow.document_vision",) if source_mode == "多模态文档" else ())
    if stage == "cpt" and source_mode != "开放需求":
        return ()
    config = (node_generation or {}).get(stage) or {}
    styled = config.get("enabled", True) if config else False
    if stage == "sft":
        return (("workflow.sft_styled", "workflow.jev_score", "workflow.style_check")
                if styled else ("workflow.sft", "workflow.jev_score"))
    if stage == "cot":
        return (("workflow.cot_generate", "workflow.rationale_check", "workflow.style_check")
                if styled else ("workflow.rationale_check",))
    return NODE_PROMPT_IDS.get(stage, ())


def validate_node_prompts(value: dict | None) -> dict:
    """Validate literal bodies; placeholders are never interpolated or executed."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > len(NODE_PROMPT_IDS):
        raise ValueError("invalid_node_prompts")
    result = {}
    for stage, templates in value.items():
        if stage not in NODE_PROMPT_IDS or not isinstance(templates, dict):
            raise ValueError("invalid_node_prompts")
        for prompt_id, text in templates.items():
            if prompt_id not in NODE_PROMPT_IDS[stage]:
                raise ValueError("invalid_node_prompt_id")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_NODE_PROMPT_CHARS or "\x00" in text:
                raise ValueError("invalid_node_prompt_text")
        if templates:
            result[stage] = dict(templates)
    return result


def snapshot_node_prompts(value: dict | None) -> dict:
    """Pin built-ins and overrides before a submitted run starts."""
    overrides = validate_node_prompts(value)
    return {stage: {prompt_id: overrides.get(stage, {}).get(prompt_id, builtin_node_prompt(prompt_id))
                    for prompt_id in prompt_ids}
            for stage, prompt_ids in NODE_PROMPT_IDS.items()}


def validate_node_prompt_snapshot(value: dict, system: str) -> dict:
    """A new recipe must carry every supported stage and its exact catalog."""
    templates = validate_node_prompts(value)
    if (set(templates) != set(NODE_PROMPT_IDS)
            or any(set(templates[stage]) != set(ids) for stage, ids in NODE_PROMPT_IDS.items())
            or not isinstance(system, str) or not system.strip() or len(system) > MAX_NODE_PROMPT_CHARS):
        raise ValueError("invalid_node_prompt_snapshot")
    return templates
