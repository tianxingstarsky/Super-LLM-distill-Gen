"""Compose one recorded Agent editor with the existing experience framework."""
from copy import deepcopy

from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
from lib.bootstrap.experience_evolution import experience_evolution_application
from lib.contexts.workflows.application.optimize_recorded_agent import OptimizeRecordedAgent
from lib.contexts.workflows.infrastructure.agent_optimization_adapter import RecordedAgentOptimizationAdapter
from lib.contexts.workflows.domain.dialogue import dialogue_digest
from lib.contexts.workflows.domain.reasoning_bank import cumulative_review_base_text, edition_reference
from lib.contexts.workflows.domain.public_experience import initial_experience_text


def freeze_agent_reasoning_memory(experience, *, google=None):
    """Pin adoption once; querying and authoring occur against this fixed version."""
    google = google or google_reasoning_bank_application()
    selected = experience["scopes"]["reasoning"]
    text, reference, projection = selected["text"], None, None
    identifier = google.active().get("scopes", {}).get("reasoning") if selected["enabled"] else None
    if identifier:
        edition = google.repository.get_version(identifier)
        if edition["scope"] != "reasoning":
            raise ValueError("experience_evolution_parent_scope_mismatch")
        reference = edition_reference(edition)
        if text == edition["text"]:
            text = cumulative_review_base_text(edition)
            if text is None:
                text = initial_experience_text("reasoning")
            projection = {"submitted_text_sha256": dialogue_digest(selected["text"]), "base_text_sha256": dialogue_digest(text)}
    body = {"version": 1, "scope": "reasoning", "enabled": selected["enabled"], "text": text,
            "edition_ref": deepcopy(reference), "base_projection": projection}
    return {**body, "sha256": dialogue_digest(body)}


def recorded_agent_optimizer(runtime):
    return OptimizeRecordedAgent(RecordedAgentOptimizationAdapter(runtime,
        google_reasoning_bank_application(), experience_evolution_application()), runtime.recipe.get("conversation_reasoning"))
