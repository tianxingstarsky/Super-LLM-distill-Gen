"""Resolve frozen style controls without loading a model client."""
from __future__ import annotations
from copy import deepcopy
from lib.domain.workflow_quality import canonical
from lib.contexts.workflows.contracts import TRAINING_FIELDS
from ..domain.automatic_rules import _authored_cot_language_v2, _authored_reasoning_language_requirements, _authored_reasoning_warmup_scope


def _current_review_generation_style(workflow, target, row, requirements):
    """Project new trusted settings without rewriting a historical candidate.

    A rework can legitimately replace an old style instruction that prohibited
    translation. Its old row and all audit/history remain intact; only the
    current model request receives the new immutable recipe's effective style.
    """
    cot = _authored_cot_language_v2(workflow.recipe, target, row)
    warmup = _authored_reasoning_warmup_scope(workflow.recipe, target, row)
    language_version = workflow.recipe.get("reasoning_language_contract_version")
    if not warmup and (language_version not in {2, 3}
            or not (cot or any(item.get("protocol_version") in {2, 3} for item in requirements))):
        return row.get("generation_style")
    stage = warmup["stage"] if warmup else "cot" if cot else target
    configs = workflow.recipe.get("node_generation", {})
    if stage == "multiturn" and stage not in configs:
        stage = "sft"
    resolver = getattr(workflow, "generation_style", None)
    if callable(resolver):
        return resolver(stage, row["id"])
    # The normal worker has the resolver above; pure review probes use the
    # same version and pin rules without constructing or loading a model client.
    from lib.domain.workflow_generation import style_for_sample, reasoning_language_instruction
    if warmup and language_version not in {1, 2, 3}:
        language_version = 1
    depth_version = workflow.recipe.get("reasoning_depth_contract_version", 0)
    style = style_for_sample(configs[stage], row["id"], reasoning_language_version=language_version,
                             reasoning_depth_version=depth_version)
    pinned = workflow.recipe.get("node_generation_prompts", {}).get(stage, {}).get(style["preset"])
    if pinned is not None:
        style["instruction"] = pinned
        language_rule = reasoning_language_instruction(style.get("reasoning_language", "auto"), version=language_version)
        if language_rule and not pinned.endswith(language_rule):
            style["instruction"] += "\n" + language_rule
    depth_rule = style.get("reasoning_depth_instruction")
    if depth_version == 1 and depth_rule and not style["instruction"].endswith(depth_rule):
        style["instruction"] += "\n" + depth_rule
    return style


def current_claim_review_requirements(workflow, target, row, *, instruction=""):
    """Read finite current node controls without promoting old QA plans.

    Native/human explanations are not relabeled as newly authored merely
    because a recipe has an active style. Only the existing trusted authoring
    scope selects assistant indices; the style text itself is resolved afresh
    from the frozen current node configuration, never the candidate's history.
    """
    requirements = {}
    revision_version = workflow.recipe.get("claim_review_revision_instruction_version")
    if revision_version is not None and (type(revision_version) is not int or revision_version != 1):
        raise ValueError("invalid_claim_review_revision_instruction_version")
    if revision_version == 1:
        if (not isinstance(instruction, str) or instruction and not instruction.strip()
                or "\x00" in instruction or len(instruction) > 16_000):
            raise ValueError("invalid_claim_review_revision_instruction")
        if instruction:
            requirements["revision_instruction"] = instruction
    director = workflow.recipe.get("qa_director", {})
    if director.get("enabled") is True and director.get("learning_goal") in {
            "knowledge_application", "document_reading", "mixed"}:
        requirements["learning_goal"] = director["learning_goal"]
    scope = _authored_reasoning_warmup_scope(workflow.recipe, target, row)
    if scope is not None:
        language = _authored_reasoning_language_requirements(workflow.recipe, target, row)
        style = _current_review_generation_style(workflow, target, row, language)
        if isinstance(style, dict):
            _validate_reasoning_depth_input_control(workflow, target, row, style)
            if target == "cot":
                from lib.domain.learner_context import learner_messages
                messages = learner_messages(target, workflow._review_payload(target, row))
                indices = [index for index, message in enumerate(messages)
                           if message.get("role") == "assistant"][-1:]
            else:
                indices = scope.get("assistant_indices", [])
            requirements["generation_style"] = {"stage": scope["stage"],
                "style": style["preset"], "instruction": style["instruction"],
                "assistant_indices": list(indices),
                **({"reasoning_language": style["reasoning_language"]} if "reasoning_language" in style else {})}
            if (workflow.recipe.get("reasoning_depth_contract_version") == 1
                    and "reasoning_depth" in style):
                # This comes from the frozen node resolver, never the row's
                # old style, an API effort label, or the model's self-report.
                requirements["generation_style"].update(
                    reasoning_depth=style["reasoning_depth"],
                    reasoning_depth_instruction=style["reasoning_depth_instruction"])
    return requirements


def _validate_reasoning_depth_input_control(workflow, target, row, style):
    """A sealed learner input cannot silently acquire a different depth.

    The configured style may govern newly authored text only when its public
    control is already part of this exact canonical training conversation.
    Ordinary generation establishes that input; same-task review/repair only
    validates it. Neither historical row metadata nor native/human text can
    independently activate this requirement.
    """
    if (workflow.recipe.get("reasoning_depth_contract_version") != 1
            or _authored_reasoning_warmup_scope(workflow.recipe, target, row) is None
            or not isinstance(style, dict) or not style.get("reasoning_depth")):
        return
    from lib.domain.learner_context import learner_messages
    from lib.domain.reasoning_depth import apply_reasoning_depth_control
    actual = learner_messages(target, workflow._review_payload(target, row))
    try:
        expected = apply_reasoning_depth_control(actual, style["reasoning_depth"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("reasoning_depth_input_control_mismatch") from error
    if canonical(actual) != canonical(expected):
        raise ValueError("reasoning_depth_input_control_mismatch")


def _record_accepted_revision_style(workflow, target, original, updated):
    """Record a successful new revision's style while keeping its old identity."""
    requirements = _authored_reasoning_language_requirements(workflow.recipe, target, updated)
    if not (_authored_cot_language_v2(workflow.recipe, target, updated)
            or any(item.get("protocol_version") in {2, 3} for item in requirements)):
        return
    old_body = {field: original[field] for field in TRAINING_FIELDS[target] if field in original}
    new_body = {field: updated[field] for field in TRAINING_FIELDS[target] if field in updated}
    if canonical(old_body) == canonical(new_body):
        return
    style = _current_review_generation_style(workflow, target, updated, requirements)
    if not isinstance(style, dict) or style == original.get("generation_style"):
        return
    from lib.contexts.review.domain.candidate_content import candidate_digest as digest
    prior = {"version": workflow.recipe.get("reasoning_language_contract_version", 2),
             "scope": "historical_style_before_accepted_revision",
             "generation_style": deepcopy(original.get("generation_style")),
             "training_record_sha256": digest(old_body),
             "replacement_training_record_sha256": digest(new_body),
             "superseded_by": "accepted_prompt_revision"}
    if isinstance(original.get("messages"), list):
        prior["messages_sha256"] = digest(original["messages"])
    history = deepcopy(updated.get("generation_style_history", []))
    if not isinstance(history, list):
        history = [history]
    updated["generation_style_history"] = [*history, prior]
    updated["generation_style"] = deepcopy(style)
