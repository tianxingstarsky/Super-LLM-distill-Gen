"""Pinned SFT/CoT authoring rules and learner-facing projections; no I/O."""
from __future__ import annotations
from pathlib import PurePath
from lib.domain.workflow_generation import style_for_sample


class EmptyVisibleAnswer(Exception):
    """A completed native answer with no visible output, eligible for bounded retry."""


def is_pdf_source(recipe, unit):
    return any(PurePath(source.get("file", "")).suffix.lower() == ".pdf"
        and source.get("sha256") == unit.get("source_id")
        and source.get("name") == unit.get("source_name")
        for source in recipe.get("sources", ()))


def generation_style(recipe, stage, sample_id):
    config = recipe.get("node_generation", {}).get(stage)
    if not config or not config.get("enabled", True):
        return None
    language_version = recipe.get("reasoning_language_contract_version", 1)
    style = style_for_sample(config, sample_id, reasoning_language_version=language_version,
        reasoning_depth_version=recipe.get("reasoning_depth_contract_version", 0))
    pinned = recipe.get("node_generation_prompts", {}).get(stage, {}).get(style["preset"])
    if pinned is not None:
        style["instruction"] = pinned
        from lib.domain.workflow_generation import reasoning_language_instruction
        language_rule = reasoning_language_instruction(style.get("reasoning_language", "auto"),
                                                        version=language_version)
        if language_rule and not pinned.endswith(language_rule):
            style["instruction"] += "\n" + language_rule
    if style.get("reasoning_depth_instruction"):
        style["instruction"] += "\n" + style["reasoning_depth_instruction"]
    return style


def qa_metadata(sample, check=None):
    if not sample.get("qa_contract"):
        return ({"human_augmentation_status": "recorded_conversation_preserved"}
                if sample.get("human_augmentation_status") == "recorded_conversation_preserved" else {})
    return {key: sample.get(key) for key in ("qa_contract", "family_id", "parent_id")} | (
        {"qa_contract_check": check} if check is not None else {})


def task_controls(contract):
    return {key: contract[key] for key in ("learning_goal", "user_language") if key in contract}


def learner_prompt(contract):
    """This is the only contract projection permitted in training messages."""
    question = contract["question"]
    if contract["visible_context"]:
        if contract.get("user_language") == "en":
            label = "User message:" if contract.get("dialogue_design") else "Question:"
            return "Source material:\n" + contract["visible_context"] + "\n\n" + label + "\n" + question
        label = "用户消息：" if contract.get("dialogue_design") else "问题："
        return "资料：\n" + contract["visible_context"] + "\n\n" + label + "\n" + question
    return question
