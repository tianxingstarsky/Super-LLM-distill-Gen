"""Reuse the shared strict-tool transport for one review with guidance outputs."""


def review_evolution_tool_transport(runtime):
    from lib.contexts.workflows.infrastructure.reasoning_agent_adapter import CheckpointedReasoningAgent
    return CheckpointedReasoningAgent(runtime)


def record_review_memory(runtime, target, original, observation):
    """Archive the same-call general proposal without loading or calling a model."""
    from copy import deepcopy
    from lib.bootstrap.google_reasoning_bank import google_reasoning_bank_application
    from lib.contexts.workflows.domain.experience_evolution import digest
    from lib.contexts.workflows.infrastructure.review_memory_proposals import record_review_memory_proposal
    if observation.get("status") != "observed" or "memory_proposal" not in observation:
        return {"version": 1, "origin": "same_review_call", "status": "not_proposed",
            "reason": "review_not_observed", "review_observation_sha256": digest(observation),
            "new_model_calls": 0, "adopted": False}
    proposal = observation.get("memory_proposal", {"status": "no_update", "memory_items": []})
    app = google_reasoning_bank_application()
    source = {"kind": "single_review_evolution", "run_id": runtime.state["id"], "target": target,
        "candidate_id": original["id"], "first_draft_sha256": digest(original),
        "review_observation_sha256": digest(observation), "review_checkpoint": deepcopy(observation.get("checkpoint", {})),
        "recipe_sha256": runtime.state.get("recipe_hash"),
        "base_experience_sha256": runtime.recipe.get("agent_experience", {}).get("sha256")}
    episode = {"id": original["id"], "conversation": {"messages": deepcopy(original.get("messages", []))},
        "review_observation": deepcopy(observation), "assessment_source": {
            "kind": "single_review_model_observation", "independently_verified": False}}
    base_text = runtime.recipe.get("agent_experience", {}).get("scopes", {}).get("reasoning", {}).get("text", "")
    return record_review_memory_proposal(app.repository, app.bank, proposal,
        source_receipt=source, base_text=base_text, episode=episode)
