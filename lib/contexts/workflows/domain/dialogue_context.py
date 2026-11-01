"""Keep synthetic scene design outside the responding participant's knowledge.

The director retains the original design. This disposable request projection
never edits stored sources, learner turns, reasoning, or review records.
"""
from copy import deepcopy


def project_answer_context(payload, source, *, version=None):
    """Project confirmed synthetic briefs, preserving legacy and real sources."""
    projected = deepcopy(payload)
    if version is None:
        return projected
    if type(version) is not int or version != 1:
        raise ValueError("invalid_dialogue_context_scope_version")
    if not isinstance(source, dict):
        return projected
    if (source.get("kind") != "brief" or source.get("synthetic") is not True
            or source.get("human_provided")):
        return projected

    # These fields describe the private writing assignment, not facts supplied
    # by the simulated user. Actual public messages already carry everything
    # that participant has chosen to disclose, including visible excerpts.
    for key in ("source", "source_context", "source_context_pack", "teacher_context_pack",
                "source_evidence", "source_lines", "source_line_scope", "source_line_ranges",
                "source_line_focus", "task", "text", "brief", "requirement", "guidance",
                "learning_goal", "dialogue_design", "expression_design", "dialogue_state",
                "dialogue_step"):
        projected.pop(key, None)
    contract = payload.get("qa_contract")
    if isinstance(contract, dict):
        projected["qa_contract"] = {key: deepcopy(contract[key]) for key in
            ("qa_type", "answer_policy", "question", "visible_context", "user_language")
            if key in contract}
        step = payload.get("dialogue_step")
        if isinstance(step, dict) and step.get("response_policy"):
            projected["qa_contract"]["answer_policy"] = step["response_policy"]
    projected["answer_context_scope"] = {
        "version": 1, "source_scope": "learner_dialogue_only",
        "private_scene": "not_provided", "user_facts": "learner_messages",
        "assistant_history": "unverified",
    }
    return projected
