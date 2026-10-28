"""Native execution facts and observable leakage; never rewrite the output."""
import re

NATIVE_EXECUTION_STATE = "native_execution_state"

_EXECUTION = re.compile(
    r"authored_task_contract|reasoning_depth_control|revision_context|revision[_ ]feedback"
    r"|显式推理语言要求|原生思考通道|输出全部原生思考"
    r"|\bsystem\s+(?:prompt|instructions?|says)\b|in this interface", re.I)
_ENVIRONMENT = re.compile(r"\bnative_execution_state\b", re.I)


def native_execution_state(recipe, task, contract):
    """Project bounded facts, never another copy of the source or user words."""
    version = recipe.get("native_environment_version")
    if version is None:
        return None
    if type(version) is not int or version != 1:
        raise ValueError("invalid_native_environment_version")
    messages = task["prompt"]
    return {"version": version,
            "current_user_turn": sum(message.get("role") == "user" for message in messages),
            "source_scope": contract["source_scope"],
            "assistant_history": "unverified" if any(message.get("role") == "assistant" for message in messages) else "absent",
            "previous_candidate": "proposal" if task.get("previous_candidate") else "absent",
            "tools": "none"}


def native_instruction_leakage(reasoning, visible_messages, *, public_task_rules=False,
                               environment_version=None, field="reasoning"):
    """Do not ban technical discussion explicitly present in the real dialogue."""
    if not isinstance(reasoning, str):
        return []
    visible = "\n".join(m.get("content", "") for m in visible_messages
                        if isinstance(m.get("content"), str)).casefold()
    evidence = []
    # Existing execution-phrase checks stay on reasoning. Only the new private
    # state marker extends to answers; do not broaden legacy answer filtering.
    patterns = (_EXECUTION,) if field == "reasoning" else ()
    if environment_version == 1:
        patterns += (_ENVIRONMENT,)
    for match in (match for pattern in patterns for match in pattern.finditer(reasoning)):
        if (public_task_rules and any(m.get("role") == "system" and m.get("content") for m in visible_messages)
                and re.fullmatch(r"system\s+(?:prompt|instructions?|says)|显式推理语言要求", match[0], re.I)):
            # A reading-session export contains the actual system rules.
            # Mentioning those rules is no longer a hidden-context leak.
            continue
        if match[0].casefold() in visible:
            continue
        start, end = max(0, match.start()-80), min(len(reasoning), match.end()+160)
        evidence.append({"issue": "native_generation_instruction_leakage", "field": field,
            "start": start, "end": end, "quote": reasoning[start:end]})
    return evidence
