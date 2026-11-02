"""Frozen per-candidate knowledge/reading selection, never a batch quota."""
from collections.abc import Mapping
from fractions import Fraction
import math

from .dialogue import dialogue_digest


def validate_learning_goal_mix(value):
    if (not isinstance(value, dict) or set(value) != {"version", "document_reading_probability"}
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["document_reading_probability"]) not in (int, float)
            or not math.isfinite(value["document_reading_probability"])
            or not 0 <= value["document_reading_probability"] <= 1):
        raise ValueError("invalid_qa_director_learning_goal_mix")
    return {"version": 1, "document_reading_probability": float(value["document_reading_probability"])}


def learning_goal_route(recipe, candidate_id, *, reading_material_available):
    policy = recipe.get("qa_director", {}).get("learning_goal_mix")
    if policy is None:
        return None
    seed = recipe.get("qa_learning_goal_mix_seed")
    if (not isinstance(seed, str) or not seed or not isinstance(candidate_id, str) or not candidate_id
            or type(reading_material_available) is not bool):
        raise ValueError("invalid_qa_learning_goal_routing_input")
    identity = dialogue_digest({"version": 1, "run_seed": seed, "candidate_id": candidate_id})
    probability = Fraction(str(policy["document_reading_probability"]))
    document = int(identity, 16) * probability.denominator < probability.numerator * (1 << 256)
    selected = "document_reading" if document else "knowledge_application"
    fallback = document and not reading_material_available
    body = {"version": 1, "selection": "candidate_hash_v1", "candidate_id": candidate_id,
        "input_sha256": identity, "document_reading_probability": policy["document_reading_probability"],
        "reading_material_available": reading_material_available, "selected_learning_goal": selected,
        "learning_goal": "knowledge_application" if fallback else selected,
        "reason": "reading_material_unavailable" if fallback else "selected"}
    return {**body, "sha256": dialogue_digest(body)}


def effective_learning_goal(recipe, contract=None):
    """Only an authenticated host routing receipt can override the frozen run goal."""
    configured = recipe.get("qa_director", {}).get("learning_goal")
    if recipe.get("qa_director", {}).get("learning_goal_mix") is None:
        return configured
    contract = contract if isinstance(contract, Mapping) else {}
    receipt = contract.get("learning_goal_routing")
    if receipt is None:
        return configured  # Imported/fixed human input was not automatically routed.
    if not isinstance(receipt, dict):
        raise ValueError("invalid_qa_learning_goal_routing_receipt")
    expected = learning_goal_route(recipe, contract.get("id"),
        reading_material_available=receipt.get("reading_material_available"))
    if receipt != expected or contract.get("learning_goal") != expected["learning_goal"]:
        raise ValueError("invalid_qa_learning_goal_routing_receipt")
    return expected["learning_goal"]


def repair_learning_goal_mix(parents):
    """Fixed tasks retain their authenticated parent's selector, never redraw."""
    routed, legacy = [], False
    for parent in parents:
        if parent.get("qa_director", {}).get("learning_goal_mix") is not None:
            routed.append(parent)
        else:
            legacy = True
    if not routed:
        return None
    if legacy:
        raise ValueError("qa_learning_goal_mix_incompatible_parents")
    first = routed[0]
    seed = first.get("qa_learning_goal_mix_seed")
    if not isinstance(seed, str) or not seed:
        raise ValueError("invalid_qa_learning_goal_routing_input")
    if any(parent.get("qa_learning_goal_mix_seed") != seed
           or parent["qa_director"]["learning_goal_mix"] != first["qa_director"]["learning_goal_mix"]
           for parent in routed):
        raise ValueError("qa_learning_goal_mix_incompatible_parents")
    return first


def routed_reading_excerpt(recipe, contract, messages):
    """Bind a new reading route to text actually present in a learner user turn."""
    contract = contract if isinstance(contract, Mapping) else {}
    if (recipe.get("qa_director", {}).get("learning_goal_mix") is None
            or contract.get("learning_goal_routing") is None
            or effective_learning_goal(recipe, contract) != "document_reading"):
        return None
    excerpt = contract.get("visible_context")
    if (not isinstance(excerpt, str) or not excerpt.strip() or not isinstance(messages, list)
            or not any(isinstance(message, Mapping) and message.get("role") == "user"
                and isinstance(message.get("content"), str) and excerpt in message["content"] for message in messages)):
        raise ValueError("native_reading_context_missing_or_changed")
    return excerpt


def project_routed_reading_request(data, contract):
    """Keep actual dialogue and editing targets, excluding private source copies."""
    from copy import deepcopy
    from .authored_task_contract import project_task_context
    from lib.domain.learner_context import learner_contract
    projected = project_task_context(data, contract)
    if isinstance(projected.get("context"), dict):
        projected["context"] = project_task_context(projected["context"], contract)
    private_fields = {
        "source", "source_context", "source_context_pack", "source_inventory", "source_evidence",
        "source_line_scope", "source_lines", "source_line_ranges", "source_line_focus", "source_line_anchor",
        "teacher_context_pack", "teacher_evidence", "teacher_evidence_blocks", "teacher_evidence_source_ids",
        "teacher_evidence_is_pack", "source_text", "document_reading", "original_source_text",
        "reading_source_text", "parsed_source_text", "quotes", "evidence_quotes", "guidance", "question_brief",
    }
    actual_fields = {"learner_messages", "prompt", "messages"}
    def without_private_sources(value):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if key in private_fields:
                    continue
                if key == "qa_contract" and isinstance(child, dict):
                    result[key] = {**learner_contract(child), **{name: deepcopy(child[name])
                        for name in ("id", "question", "visible_context") if name in child}}
                else:
                    result[key] = deepcopy(child) if key in actual_fields else without_private_sources(child)
            return result
        if isinstance(value, (list, tuple)):
            return [without_private_sources(child) for child in value]
        return value
    return without_private_sources(projected)
