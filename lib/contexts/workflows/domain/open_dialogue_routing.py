"""Choose a question author from frozen configuration and source ownership."""
from __future__ import annotations


def validate_open_dialogue_question_mode(value):
    """Validate an optional creation choice without changing the legacy default."""
    if value is None or type(value) is str and value in {"auto", "separated"}:
        return value
    raise ValueError("invalid_open_dialogue_question_mode")


def open_dialogue_question_route(recipe, source):
    """Return a routing receipt, not a claim about question or answer quality.

    Opt-in is explicit. Historical question separation and source contracts are
    untouched unless this new recipe asks for source-aware automatic routing.
    """
    version = recipe.get("open_dialogue_routing_version")
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ValueError("invalid_open_dialogue_routing_version")
    mode = validate_open_dialogue_question_mode(recipe.get("open_dialogue_question_mode"))
    if mode is None:
        raise ValueError("invalid_open_dialogue_question_mode")

    contract = source.get("qa_contract") or {}
    private_inputs = ("source_context_pack", "knowledge_agent_context", "source_line_material_id",
                      "source_line_ranges", "source_line_focus", "tools", "human_design",
                      "reference_answer", "source_evidence", "teacher_context_pack", "messages")
    director_config = recipe.get("qa_director", {})
    pure_scene = (source.get("kind") == "brief" and source.get("synthetic") is True
                  and not source.get("human_provided")
                  and not director_config.get("human_augmentation", {}).get("enabled")
                  and not any(source.get(key) or contract.get(key) for key in private_inputs))
    legacy_separation = director_config.get("question_separation") is True
    separated = mode == "separated" or (legacy_separation and not pure_scene)
    reason = ("explicit_separation" if mode == "separated" else
              "synthetic_scene_without_attached_evidence" if pure_scene else "preserved_source_policy")
    return {"version": version, "mode": mode,
            "route": "source_blind_questioner" if separated else "director_direct",
            "reason": reason, "quality_status": "not_assessed"}
