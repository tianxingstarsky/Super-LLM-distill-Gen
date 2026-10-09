"""Literal prompt templates bound to individual workflow nodes."""
from __future__ import annotations

from lib.prompts import get, render
from lib.domain.workflow_package_review import validate_package_review


MAX_NODE_PROMPT_CHARS = 32_768
# The exact version 9 catalog remains separate from later additions so that
# older immutable recipe snapshots can still be validated without migration.
LEGACY_NODE_PROMPT_IDS = {
    "ingest": ("workflow.plan", "workflow.document_vision"),
    "cpt": ("workflow.corpus", "workflow.jev_score"),
    "sft": ("workflow.sft", "workflow.sft_styled", "workflow.jev_score", "workflow.style_check"),
    "multiturn": ("workflow.multiturn_user", "workflow.multiturn_assistant",
                  "workflow.jev_score", "workflow.multiturn_consistency"),
    "preference": ("workflow.alternative", "workflow.jev_score"),
    "cot": ("workflow.cot_generate", "workflow.rationale_check", "workflow.style_check"),
    "trim": ("workflow.trim", "workflow.trim_check", "workflow.trim_rules_check"),
}
VERSION_10_NODE_PROMPT_IDS = {**LEGACY_NODE_PROMPT_IDS, "package": ("workflow.package_review",)}
VERSION_13_NODE_PROMPT_IDS = {
    **VERSION_10_NODE_PROMPT_IDS,
    "director": ("workflow.qa_director",),
    "sft": (*LEGACY_NODE_PROMPT_IDS["sft"], "workflow.sft_directed", "workflow.sft_directed_check"),
    "multiturn": (*LEGACY_NODE_PROMPT_IDS["multiturn"], "workflow.multiturn_directed_check"),
    "preference": (*LEGACY_NODE_PROMPT_IDS["preference"], "workflow.preference_directed_check"),
    "cot": (*LEGACY_NODE_PROMPT_IDS["cot"], "workflow.cot_directed_check"),
    "trim": (*LEGACY_NODE_PROMPT_IDS["trim"], "workflow.trim_directed_check"),
}
NODE_PROMPT_IDS = {**VERSION_13_NODE_PROMPT_IDS,
                   "ingest": (*LEGACY_NODE_PROMPT_IDS["ingest"], "workflow.document_parse"),
                   "cpt": (*LEGACY_NODE_PROMPT_IDS["cpt"], "workflow.cpt_clean", "workflow.cpt_review")}


def builtin_node_prompt(prompt_id: str) -> str:
    """Return editable text, with JSON braces already rendered exactly once."""
    if not any(prompt_id in ids for ids in NODE_PROMPT_IDS.values()):
        raise ValueError("invalid_node_prompt_id")
    return render(get(prompt_id))


def active_node_prompt_ids(stage: str, source_mode: str, *, node_generation=None,
                           package_review=None, qa_director=None, cpt_processing=None) -> tuple[str, ...]:
    """Only offer templates that the selected node can actually call."""
    # Prompt editing must remain available while users are clearing a rule or
    # adjusting the final type weight. Creation validates the complete recipe.
    directed = isinstance(qa_director, dict) and qa_director.get("enabled") is True
    if stage == "package":
        return NODE_PROMPT_IDS[stage] if validate_package_review(package_review)["enabled"] else ()
    if stage == "ingest":
        return (("workflow.plan",) if source_mode == "开放需求" else
                ("workflow.document_parse",) if source_mode == "模型辅助文档" else
                ("workflow.document_vision",) if source_mode == "多模态文档" else ())
    if stage == "cpt":
        if source_mode == "开放需求":
            return LEGACY_NODE_PROMPT_IDS["cpt"]
        from lib.domain.cpt_processing import validate_cpt_processing
        return (("workflow.cpt_clean", "workflow.cpt_review")
                if validate_cpt_processing(cpt_processing)["mode"] == "model" else ())
    config = (node_generation or {}).get(stage) or {}
    styled = config.get("enabled", True) if config else False
    if stage == "sft":
        if directed:
            return (("workflow.sft_directed", "workflow.sft_directed_check", "workflow.jev_score", "workflow.style_check")
                    if styled else ("workflow.sft_directed", "workflow.sft_directed_check", "workflow.jev_score"))
        return (("workflow.sft_styled", "workflow.jev_score", "workflow.style_check")
                if styled else ("workflow.sft", "workflow.jev_score"))
    if stage == "multiturn":
        return (NODE_PROMPT_IDS[stage] if directed
                else LEGACY_NODE_PROMPT_IDS[stage])
    if stage == "cot":
        prompts = (("workflow.cot_generate", "workflow.rationale_check", "workflow.style_check")
                   if styled else ("workflow.rationale_check",))
        return (*prompts, "workflow.cot_directed_check") if directed else prompts
    if stage in {"preference", "trim"}:
        return NODE_PROMPT_IDS[stage] if directed else LEGACY_NODE_PROMPT_IDS[stage]
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


def _prompt_catalog(recipe_version: int) -> dict:
    return (LEGACY_NODE_PROMPT_IDS if recipe_version == 9 else
            VERSION_10_NODE_PROMPT_IDS if recipe_version == 10 else
            VERSION_13_NODE_PROMPT_IDS if recipe_version <= 13 else NODE_PROMPT_IDS)


def snapshot_node_prompts(value: dict | None, *, recipe_version: int = 11) -> dict:
    """Pin built-ins and overrides before a submitted run starts."""
    overrides = validate_node_prompts(value)
    return {stage: {prompt_id: overrides.get(stage, {}).get(prompt_id, builtin_node_prompt(prompt_id))
                    for prompt_id in prompt_ids}
            for stage, prompt_ids in _prompt_catalog(recipe_version).items()}


def validate_node_prompt_snapshot(value: dict, system: str, *, recipe_version: int = 11) -> dict:
    """A new recipe must carry every supported stage and its exact catalog."""
    templates = validate_node_prompts(value)
    catalog = _prompt_catalog(recipe_version)
    if (set(templates) != set(catalog)
            or any(set(templates[stage]) != set(ids) for stage, ids in catalog.items())
            or not isinstance(system, str) or not system.strip() or len(system) > MAX_NODE_PROMPT_CHARS):
        raise ValueError("invalid_node_prompt_snapshot")
    return templates
